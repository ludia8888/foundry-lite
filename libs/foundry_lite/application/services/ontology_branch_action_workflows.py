"""Durable branch-first Action Type authoring workflows.

The branch row remains the working-copy source of truth. A separate immutable
idempotency ledger binds tenant, actor, branch, operation, key, request
fingerprint, and response in the same transaction as the branch CAS, audit,
and outbox writes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Protocol, cast

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.ontology_branch_repository import (
    OntologyBranchActionIdempotencyRecord,
    OntologyBranchActionIdempotencyRow,
    OntologyBranchRepository,
    OntologyBranchRow,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.ontology_branch_action_types import (
    ActionTypeMutation,
    branch_action_type_item,
    branch_action_type_items,
    create_branch_action_type,
    delete_branch_action_type,
    update_branch_action_type,
)
from foundry_lite.application.services.ontology_branch_payloads import (
    branch_payload,
    require_branch_fingerprint_match,
    require_open_branch,
    required_text,
)
from foundry_lite.application.services.ontology_proposal_payloads import yaml_fingerprint
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.action_permissions import can_access_action, require_action_access
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService

_RESOURCE_TYPE = "ontology_branch"


class OntologyBranchActionBoundary(Protocol):
    engine: TransactionManager
    policy: PolicyService
    runtime_service: OntologyRuntimeBoundary
    ontology_service: OntologyService
    ontology_branch_repository: OntologyBranchRepository

    def _active_version_id(self, conn: TransactionContext, ctx: RequestContext) -> str | None: ...

    def _audit_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        row: OntologyBranchRow,
        *,
        before: OntologyBranchRow | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None: ...

    def _is_base_stale(self, row: OntologyBranchRow, active_version_id: str | None) -> bool: ...

    def _require_branch_row(
        self, conn: TransactionContext, ctx: RequestContext, branch_id: str
    ) -> OntologyBranchRow: ...

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None: ...


def list_branch_action_types(
    service: OntologyBranchActionBoundary,
    branch_id: str,
    *,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    service.policy.require(ctx, "ontology:validate")
    with service.engine.begin() as conn:
        row = service._require_branch_row(conn, ctx, branch_id)
    items = branch_action_type_items(row["content_yaml_text"])
    return {
        "branchId": row["id"],
        "branchFingerprint": row["content_fingerprint"],
        "items": [item for item in items if _can_view_definition(ctx, item["definition"])],
    }


def get_branch_action_type(
    service: OntologyBranchActionBoundary,
    branch_id: str,
    api_name: str,
    *,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    service.policy.require(ctx, "ontology:validate")
    with service.engine.begin() as conn:
        row = service._require_branch_row(conn, ctx, branch_id)
    item = branch_action_type_item(row["content_yaml_text"], api_name)
    definition = cast(Mapping[str, object], item["definition"])
    require_action_access(ctx, api_name, compile_action_contract(definition).permissions, "view")
    return item


def mutate_branch_action_type(
    service: OntologyBranchActionBoundary,
    branch_id: str,
    *,
    api_name: str | None,
    definition: Mapping[str, object] | None,
    expected_fingerprint: str,
    idempotency_key: str,
    operation: str,
    ctx: RequestContext | None,
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    service.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, branch_id)
    service._require_write_open(ctx, f"{operation}_ontology_branch_action_type", branch_id)
    expected = required_text(expected_fingerprint, "expectedFingerprint")
    key = required_text(idempotency_key, "Idempotency-Key")
    action_name = _action_name(api_name, definition)
    operation_key = f"{operation}:{action_name}"
    row = _read_open_branch(service, ctx, branch_id)
    _require_mutation_access(service, ctx, row, operation, api_name, definition)
    request_fingerprint = _request_fingerprint(ctx, operation_key, expected, definition)
    replay = _read_replay(service, ctx, branch_id, operation_key, key, request_fingerprint)
    if replay is not None:
        return replay
    mutation = _action_mutation(row, operation, api_name, definition)
    if not mutation.is_replay:
        service.ontology_service.validate_yaml_text(mutation.yaml_text, ctx=ctx)
    return _commit_mutation(
        service,
        ctx,
        branch_id,
        api_name,
        definition,
        expected,
        key,
        operation,
        operation_key,
        request_fingerprint,
        action_name,
    )


def _can_view_definition(ctx: RequestContext, definition: object) -> bool:
    if not isinstance(definition, Mapping):
        return False
    contract = compile_action_contract(definition)
    return can_access_action(ctx, contract.permissions, "view")


def _require_mutation_access(
    service: OntologyBranchActionBoundary,
    ctx: RequestContext,
    row: OntologyBranchRow,
    operation: str,
    api_name: str | None,
    definition: Mapping[str, object] | None,
) -> None:
    """Require Edit on the current and replacement definitions before branch mutation."""
    action_name = _action_name(api_name, definition)
    contracts = []
    if operation != "create":
        try:
            current = branch_action_type_item(row["content_yaml_text"], action_name)
        except NotFound:  # A replayed delete has no current definition to re-authorize.
            current = None
        if current is not None:
            contracts.append(compile_action_contract(cast(Mapping[str, object], current["definition"])))
    if definition is not None:
        contracts.append(compile_action_contract(definition))
    try:
        for contract in contracts:
            require_action_access(ctx, contract.api_name, contract.permissions, "edit")
    except PermissionDenied:
        with service.engine.begin() as conn:
            service.runtime_service._audit(
                conn,
                ctx,
                event_type="permission.denied",
                resource_type="action_type",
                resource_id=action_name,
                action=f"{operation}_branch_action_type",
                decision="deny",
                after_ref={"permission": f"action:edit:{action_name}", "branchId": row["id"]},
            )
        raise


def _commit_mutation(
    service: OntologyBranchActionBoundary,
    ctx: RequestContext,
    branch_id: str,
    api_name: str | None,
    definition: Mapping[str, object] | None,
    expected: str,
    key: str,
    operation: str,
    operation_key: str,
    request_fingerprint: str,
    action_name: str,
) -> dict[str, object]:
    with service.engine.begin() as conn:
        replay = _transaction_replay(service, conn, ctx, operation_key, key, request_fingerprint, branch_id=branch_id)
        if replay is not None:
            return replay
        before = service._require_branch_row(conn, ctx, branch_id)
        require_open_branch(before)
        require_branch_fingerprint_match(before, expected)
        mutation = _action_mutation(before, operation, api_name, definition)
        after = _apply_content(service, conn, ctx, branch_id, before, mutation, expected)
        if after is None:
            return _resolve_cas_miss(service, conn, ctx, branch_id, operation_key, key, request_fingerprint, expected)
        response = _mutation_payload(service, conn, ctx, after, mutation)
        existing = _store_idempotency(service, conn, ctx, branch_id, operation_key, key, request_fingerprint, response)
        if existing is not None:
            return _replay_payload(existing, request_fingerprint)
        if not mutation.is_replay:
            _emit_evidence(service, conn, ctx, before, after, operation, key, action_name, mutation)
        return response


def _apply_content(
    service: OntologyBranchActionBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    branch_id: str,
    before: OntologyBranchRow,
    mutation: ActionTypeMutation,
    expected: str,
) -> OntologyBranchRow | None:
    if mutation.is_replay:
        return before
    return service.ontology_branch_repository.update_branch_content(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        branch_id=branch_id,
        expected_fingerprint=expected,
        content_yaml_text=mutation.yaml_text,
        content_fingerprint=yaml_fingerprint(mutation.yaml_text),
        updated_at=_now(),
    )


def _resolve_cas_miss(
    service: OntologyBranchActionBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    branch_id: str,
    operation: str,
    key: str,
    request_fingerprint: str,
    expected: str,
) -> dict[str, object]:
    replay = _transaction_replay(service, conn, ctx, operation, key, request_fingerprint, branch_id=branch_id)
    if replay is not None:
        return replay
    current = service._require_branch_row(conn, ctx, branch_id)
    require_open_branch(current)
    require_branch_fingerprint_match(current, expected)
    raise ConflictDetected("ontology branch changed concurrently during Action Type mutation")


def _mutation_payload(
    service: OntologyBranchActionBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    row: OntologyBranchRow,
    mutation: ActionTypeMutation,
) -> dict[str, object]:
    active_version_id = service._active_version_id(conn, ctx)
    return {
        "branch": branch_payload(row, is_base_stale=service._is_base_stale(row, active_version_id)),
        "actionType": mutation.action_type,
        "isReplay": mutation.is_replay,
    }


def _store_idempotency(
    service: OntologyBranchActionBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    branch_id: str,
    operation: str,
    key: str,
    request_fingerprint: str,
    response: dict[str, object],
) -> OntologyBranchActionIdempotencyRow | None:
    return service.ontology_branch_repository.insert_action_idempotency_or_existing(
        transaction=conn,
        record=OntologyBranchActionIdempotencyRecord(
            record_id=_new_id("ontbranchactionreq"),
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.actor_user_id,
            branch_id=branch_id,
            operation=operation,
            idempotency_key=key,
            request_fingerprint=request_fingerprint,
            response_json=response,
            created_at=_now(),
        ),
    )


def _emit_evidence(
    service: OntologyBranchActionBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    before: OntologyBranchRow,
    after: OntologyBranchRow,
    operation: str,
    key: str,
    action_name: str,
    mutation: ActionTypeMutation,
) -> None:
    extra = {"actionType": action_name, "idempotencyKey": key}
    service._audit_event(conn, ctx, f"action_type.{operation}", after, before=before, extra=extra)
    service.runtime_service._outbox(
        conn,
        ctx,
        f"ontology.branch.action_type.{operation}",
        _RESOURCE_TYPE,
        after["id"],
        _outbox_payload(after, action_name, mutation),
        idempotency_key=f"ontology.branch.action_type:{after['id']}:{operation}:{key}",
        correlation_id=ctx.request_id,
    )


def _outbox_payload(
    row: OntologyBranchRow,
    action_name: str,
    mutation: ActionTypeMutation,
) -> dict[str, object]:
    action_type = mutation.action_type or {}
    return {
        "branchId": row["id"],
        "branchFingerprint": row["content_fingerprint"],
        "actionType": action_name,
        "contractFingerprint": action_type.get("contractFingerprint"),
    }


def _read_replay(
    service: OntologyBranchActionBoundary,
    ctx: RequestContext,
    branch_id: str,
    operation: str,
    key: str,
    request_fingerprint: str,
) -> dict[str, object] | None:
    with service.engine.begin() as conn:
        return _transaction_replay(service, conn, ctx, operation, key, request_fingerprint, branch_id=branch_id)


def _transaction_replay(
    service: OntologyBranchActionBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    operation: str,
    key: str,
    request_fingerprint: str,
    *,
    branch_id: str = "",
) -> dict[str, object] | None:
    if not branch_id:
        return None
    row = service.ontology_branch_repository.action_idempotency_record(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        branch_id=branch_id,
        operation=operation,
        idempotency_key=key,
    )
    return _replay_payload(row, request_fingerprint) if row is not None else None


def _replay_payload(
    row: OntologyBranchActionIdempotencyRow,
    request_fingerprint: str,
) -> dict[str, object]:
    if row["request_fingerprint"] != request_fingerprint:
        raise ConflictDetected("Idempotency-Key was reused for a different branch Action Type request")
    response = dict(row["response_json"])
    response["isReplay"] = True
    return response


def _read_open_branch(
    service: OntologyBranchActionBoundary,
    ctx: RequestContext,
    branch_id: str,
) -> OntologyBranchRow:
    with service.engine.begin() as conn:
        row = service._require_branch_row(conn, ctx, branch_id)
        require_open_branch(row)
        return row


def _action_mutation(
    row: OntologyBranchRow,
    operation: str,
    api_name: str | None,
    definition: Mapping[str, object] | None,
) -> ActionTypeMutation:
    if operation == "created" and definition is not None:
        return create_branch_action_type(row["content_yaml_text"], definition)
    if operation == "updated" and definition is not None and api_name is not None:
        return update_branch_action_type(row["content_yaml_text"], api_name, definition)
    if operation == "deleted" and api_name is not None:
        return delete_branch_action_type(row["content_yaml_text"], api_name)
    raise ValidationFailed("unsupported ontology branch Action Type mutation")


def _action_name(api_name: str | None, definition: Mapping[str, object] | None) -> str:
    value = api_name if api_name is not None else definition.get("apiName") if definition else None
    return required_text(value if isinstance(value, str) else "", "Action Type apiName")


def _request_fingerprint(
    ctx: RequestContext,
    operation: str,
    expected_fingerprint: str,
    definition: Mapping[str, object] | None,
) -> str:
    payload = {
        "actorUserId": ctx.actor_user_id,
        "applicationId": ctx.application_id,
        "clientId": ctx.client_id,
        "definition": dict(definition) if definition is not None else None,
        "expectedFingerprint": expected_fingerprint,
        "operation": operation,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{sha256(raw).hexdigest()}"
