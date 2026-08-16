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

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import LinkTypeRow, ObjectRecordRow, TransactionContext
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
    webhook_response_values: Mapping[str, object] | None = None

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
        if attribute in (None, "id"):
            return self.ctx.actor_user_id
        if attribute in {"group", "groups", "roles"}:
            return list(self.ctx.roles)
        raise ValidationFailed(
            "unsupported current-user action value attribute",
            details={"attribute": attribute},
        )

    def current_time(self, unit: str) -> str:
        now = _now()
        return now[:10] if unit == "date" else now

    def generated_id(self, strategy: str) -> str:
        return _new_id(strategy or "gen")

    def webhook_response(self, field: str) -> object:
        if self.webhook_response_values is None:
            return {"$foundryDeferredSource": f"beforeEffect.response.{field}"}
        if field not in self.webhook_response_values:
            raise ValidationFailed("before-commit webhook response field is missing", details={"field": field})
        return self.webhook_response_values[field]

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
        object_type, object_id = _object_reference(self.evaluate(expression))
        if object_type is not None and object_type != endpoint_type:
            raise ValidationFailed(
                "link endpoint object type does not match the concrete link",
                details={"expectedObjectType": endpoint_type, "objectType": object_type, "role": role},
            )
        return self._require_ref(endpoint_type, object_id).object_id

    def resolve_interface_link_deletes(
        self,
        link_types: tuple[str, ...],
        source: ValueExpression,
        target: ValueExpression,
    ) -> tuple[tuple[str, str, str], ...]:
        source_type, source_id = _object_reference(self.evaluate(source))
        target_type, target_id = _object_reference(self.evaluate(target))
        metas = tuple(self.link_type_lookup.link_type(self.conn, self.ctx, name) for name in link_types)
        selected = _matching_interface_links(metas, source_type, target_type)
        self._require_unambiguous_interface_target(selected, target_type)
        return tuple(self._resolved_link_delete(meta, source_id, target_id) for meta in selected)

    def _require_unambiguous_interface_target(self, metas: tuple[LinkTypeRow, ...], target_type: str | None) -> None:
        if target_type is not None:
            return
        concrete_targets = {meta["to_api_name"] for meta in metas}
        if len(concrete_targets) > 1:
            raise ValidationFailed(
                "interface link delete needs a typed target reference",
                details={"candidateObjectTypes": sorted(concrete_targets)},
            )

    def _resolved_link_delete(self, link: LinkTypeRow, source_id: str, target_id: str) -> tuple[str, str, str]:
        source_type = link["from_api_name"]
        target_type = link["to_api_name"]
        self._require_ref(source_type, source_id)
        self._require_ref(target_type, target_id)
        return link["api_name"], source_id, target_id

    def generate_object_id(self, rule_id: str) -> str:
        del rule_id
        return _new_id("obj")

    def operation_key(self, rule_id: str, discriminator: str) -> str:
        return f"{self.command.idempotency_key}:{rule_id}:{discriminator}"

    # --- internals --------------------------------------------------------------
    def _visible_record(self, object_type: str, object_id: str) -> ObjectRecordRow | None:
        record = self.object_lookup._object_record(self.conn, self.ctx, object_type, object_id)
        target_type = self.ontology_lookup._active_object_type(self.conn, self.ctx, object_type)
        return visible_record(
            record,
            target_type,
            self.ctx.roles,
            self.ontology_lookup._properties_for_object_type(self.conn, target_type["id"]),
        )

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


def _object_reference(value: object) -> tuple[str | None, str]:
    if isinstance(value, Mapping):
        object_type = value.get("objectType")
        object_id = value.get("objectId")
        if not isinstance(object_type, str) or not object_type or not isinstance(object_id, str) or not object_id:
            raise ValidationFailed("typed object reference requires objectType and objectId")
        return object_type, object_id
    if isinstance(value, str) and value:
        return None, value
    raise ValidationFailed("object reference must be an object id or typed object reference")


def _matching_interface_links(
    metas: tuple[LinkTypeRow, ...], source_type: str | None, target_type: str | None
) -> tuple[LinkTypeRow, ...]:
    selected = tuple(
        meta
        for meta in metas
        if (source_type is None or meta["from_api_name"] == source_type)
        and (target_type is None or meta["to_api_name"] == target_type)
    )
    if not selected:
        raise ValidationFailed(
            "typed interface link references do not match a concrete implementation",
            details={"sourceObjectType": source_type, "targetObjectType": target_type},
        )
    return selected
