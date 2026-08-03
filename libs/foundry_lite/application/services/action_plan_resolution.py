"""Live resolution context binding an apply request to the Action IR v2 planner.

``build_edit_plan`` and ``evaluate_value`` take injected resolution protocols so
the domain stays pure. This adapter is the application-side implementation used on
the real apply path: it evaluates value expressions from the request (parameters,
the reserved ``__target__`` object, current user/time, generated ids) and resolves
existing objects through the object read service, always honoring row/segment
visibility (a hidden row resolves to NotFound, never a silent write). It performs
no writes and holds no mutable state; one instance is built per apply request.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import ObjectRecordRow, TransactionContext
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_edit_plan_committer import CommitLinkTypeResolver
from foundry_lite.application.services.action_ir_compiler import V1_TARGET_PARAMETER
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup, ActionOntologyLookup
from foundry_lite.application.services.object_store.row_policies import visible_record
from foundry_lite.domain.action_runtime.edit_plan import ObjectRef
from foundry_lite.domain.action_runtime.value_expression import ValueExpression, evaluate_value
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


@dataclass(frozen=True)
class LivePlanResolutionContext:
    """Bind one apply request's data to the value/plan resolution protocols."""

    conn: TransactionContext
    ctx: RequestContext
    command: ActionApplyCommand
    object_lookup: ActionObjectRecordLookup
    ontology_lookup: ActionOntologyLookup
    link_type_lookup: CommitLinkTypeResolver

    # --- ValueResolutionContext -------------------------------------------------
    def parameter(self, name: str) -> object:
        if name == V1_TARGET_PARAMETER:
            return self.command.object_id
        return self.command.params.get(name)

    def object_property(self, parameter: str, prop: str) -> object:
        if parameter != V1_TARGET_PARAMETER:
            raise ValidationFailed(
                "object property source requires a typed object parameter (not yet supported)",
                details={"parameter": parameter},
            )
        record = self._visible_record(self.command.object_type, self.command.object_id)
        if record is None:
            raise NotFound("target object not found", details={"objectId": self.command.object_id})
        return record["properties"].get(prop)

    def prior_rule_output(self, rule_id: str, output: str) -> object:
        raise ValidationFailed(
            "prior-rule output is only usable as a link endpoint in this version",
            details={"ruleId": rule_id, "output": output},
        )

    def function_output(self, key: str) -> object:
        raise ValidationFailed("function-backed value sources are not yet supported", details={"key": key})

    def current_user(self, attribute: str | None) -> object:
        if attribute is not None:
            raise ValidationFailed(
                "current-user attribute/group value sources are not yet supported",
                details={"attribute": attribute},
            )
        return self.ctx.actor_user_id

    def current_time(self, unit: str) -> str:
        now = _now()
        return now[:10] if unit == "date" else now

    def generated_id(self, strategy: str) -> str:
        return _new_id(strategy or "gen")

    def webhook_response(self, field: str) -> object:
        raise ValidationFailed("webhook response value sources are not yet supported", details={"field": field})

    # --- PlanResolutionContext --------------------------------------------------
    def evaluate(self, expression: ValueExpression) -> object:
        return evaluate_value(expression, self)

    def resolve_existing_object(self, object_type: str, expression: ValueExpression) -> ObjectRef:
        return self._require_ref(object_type, str(self.evaluate(expression)))

    def resolve_existing_object_set(self, object_type: str, expression: ValueExpression) -> tuple[ObjectRef, ...]:
        value = self.evaluate(expression)
        if isinstance(value, str) or not isinstance(value, list | tuple):
            raise ValidationFailed(
                "many-cardinality target must resolve to a list of object ids",
                details={"objectType": object_type, "valueType": type(value).__name__},
            )
        return tuple(self._require_ref(object_type, str(object_id)) for object_id in value)

    def resolve_link_endpoint(self, link_type: str, role: str, expression: ValueExpression) -> str:
        # An existing link endpoint is resolved (and visibility-checked) through the
        # endpoint's declared object type so a hidden/missing row raises instead of
        # writing a link to a row the caller cannot see (or to a null-coerced id).
        meta = self.link_type_lookup.link_type(self.conn, self.ctx, link_type)
        endpoint_type = meta["from_api_name"] if role == "source" else meta["to_api_name"]
        return self._require_ref(endpoint_type, str(self.evaluate(expression))).object_id

    def generate_object_id(self, rule_id: str) -> str:
        del rule_id
        return _new_id("obj")

    def operation_key(self, rule_id: str, discriminator: str) -> str:
        return f"{self.command.idempotency_key}:{rule_id}:{discriminator}"

    # --- internals --------------------------------------------------------------
    def _visible_record(self, object_type: str, object_id: str) -> ObjectRecordRow | None:
        record = self.object_lookup._object_record(self.conn, self.ctx, object_type, object_id)
        target_type = self.ontology_lookup._active_object_type(self.conn, self.ctx, object_type)
        return visible_record(record, target_type, self.ctx.roles)

    def _require_ref(self, object_type: str, object_id: str) -> ObjectRef:
        record = self._visible_record(object_type, object_id)
        if record is None:
            raise NotFound("object not found", details={"objectType": object_type, "objectId": object_id})
        version = self._target_bound_version(object_type, object_id, record["object_version"])
        return ObjectRef(object_type=object_type, object_id=object_id, version=version)

    def _target_bound_version(self, object_type: str, object_id: str, read_version: int) -> int:
        # The primary target's edit CASes on the client-supplied expected version, not a
        # fresh read: this closes a TOCTOU window where a concurrent bump between the
        # pre-commit version check and this read would otherwise let a stale write win.
        if object_type != self.command.object_type or object_id != self.command.object_id:
            return read_version
        if read_version != self.command.expected_object_version:
            raise ConflictDetected(
                "object version conflict",
                details={
                    "currentObjectVersion": read_version,
                    "expectedObjectVersion": self.command.expected_object_version,
                },
            )
        return self.command.expected_object_version
