from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

import pytest
from foundry_lite.application.ports.action_repository import ActionRunRow, ActionWritebackRecord
from foundry_lite.application.ports.external_writeback_adapter import (
    RemoteOutcome,
    RemoteOutcomeStatus,
)
from foundry_lite.application.services.action_reconciliation import (
    ActionWritebackReconciliationWorkflow,
    _required_approval_text,
)
from foundry_lite.application.services.action_reconciliation_helpers import (
    already_reconciled_result,
    is_resolvable_writeback,
    queue_item,
    queue_statuses,
    reconciled_result,
    reconciled_writeback_response,
    validate_remote_success,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed


def test_validate_remote_success_rejects_unresolved_remote_outcomes() -> None:
    with pytest.raises(ValidationFailed, match="only remote success"):
        validate_remote_success("failed", "crm-1")
    with pytest.raises(ValidationFailed, match="remote resource id is required"):
        validate_remote_success("succeeded", "")


def test_reconciled_writeback_response_preserves_vendor_evidence() -> None:
    response = reconciled_writeback_response(
        _writeback(response={"vendorTraceId": "trace-1", "status_code": 202}),
        "succeeded",
        "remote-1",
        "2026-06-19T00:00:00Z",
    )

    assert response == {
        "vendorTraceId": "trace-1",
        "status_code": 200,
        "outcome_unknown": False,
        "reconciled": True,
        "remote_resource_id": "remote-1",
        "last_observed_status": "succeeded",
        "reconciled_at": "2026-06-19T00:00:00Z",
    }


def test_reconciliation_results_include_mutation_and_idempotent_replay_evidence() -> None:
    reconciled = reconciled_result(
        "run-1",
        "writeback-1",
        "succeeded",
        "remote-1",
        {
            "actionRunId": "run-1",
            "status": "succeeded",
            "target": {"objectId": "order-1"},
            "objectEditId": "edit-1",
            "newObjectVersion": 3,
        },
    )
    already = already_reconciled_result(
        _writeback(
            response={
                "last_observed_status": "succeeded",
                "remote_resource_id": "persisted-remote",
            }
        ),
        "succeeded",
        "caller-remote",
    )

    assert reconciled.get("objectEditId") == "edit-1"
    assert reconciled.get("newObjectVersion") == 3
    assert already == {
        "actionRunId": "run-1",
        "writebackId": "writeback-1",
        "status": "reconciled",
        "remoteStatus": "succeeded",
        "remoteResourceId": "persisted-remote",
        "alreadyReconciled": True,
    }


def test_terminal_action_run_is_not_treated_as_reconciliation_candidate() -> None:
    assert not is_resolvable_writeback(_writeback(), _action_run(status="succeeded"))
    assert is_resolvable_writeback(_writeback(), _action_run(status="outcome_unknown"))
    assert not is_resolvable_writeback(_writeback(status="retryable"), _action_run(status="retryable"))


def test_reconciliation_queue_rejects_terminal_status_filters() -> None:
    with pytest.raises(ValidationFailed, match="must be unresolved"):
        queue_statuses("reconciled")
    assert queue_statuses("retryable") == ("retryable",)


def test_reconciliation_queue_item_masks_sensitive_payload() -> None:
    item = queue_item(
        _writeback(
            response={
                "last_observed_status": "unknown",
                "remote_resource_id": None,
                "reconciliation_deadline": "2026-06-19T00:10:00Z",
                "margin": 42,
            }
        ),
        {"margin"},
    )

    assert item["status"] == "outcome_unknown"
    assert item["reconciliationDeadline"] == "2026-06-19T00:10:00Z"
    assert item["response"] is not None
    assert item["response"]["margin"] == "***MASKED***"


class _Engine:
    @contextmanager
    def begin(self):
        yield object()


class _Repository:
    def __init__(
        self,
        *,
        writeback: ActionWritebackRecord | None = None,
        action_run: ActionRunRow | None = None,
    ) -> None:
        self.writeback = writeback
        self.action_run = action_run

    def action_writeback_by_id(self, **_kwargs: object) -> ActionWritebackRecord | None:
        return self.writeback

    def action_run_by_id(self, **_kwargs: object) -> ActionRunRow | None:
        return self.action_run


class _Policy:
    def __init__(self, sensitive: set[str] | None = None, *, is_denied: bool = False) -> None:
        self.sensitive = sensitive or set()
        self.is_denied = is_denied

    def sensitive_column_names(self, _ctx: RequestContext) -> set[str]:
        return self.sensitive

    def require(self, _ctx: RequestContext, _permission: str) -> None:
        if self.is_denied:
            raise PermissionDenied("denied")


class _Runtime:
    def __init__(self) -> None:
        self.audits: list[dict[str, object]] = []

    def _audit(self, *_args: object, **kwargs: object) -> None:
        self.audits.append(dict(kwargs))


class _ExternalAdapter:
    profile_name = "fake"

    def __init__(self, outcome: RemoteOutcome) -> None:
        self.outcome = outcome

    def remote_lookup(self, _target: object) -> RemoteOutcome:
        return self.outcome


class _Ontology:
    def _action_type_by_id(self, *_args: object) -> dict[str, object]:
        return {"id": "atype-1"}


class _Objects:
    def __init__(self, record: dict[str, object] | None) -> None:
        self.record = record

    def _object_record(self, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        return self.record


def _workflow(
    repository: _Repository | None = None,
    *,
    policy: _Policy | None = None,
    objects: _Objects | None = None,
    external: _ExternalAdapter | None = None,
    runtime: _Runtime | None = None,
) -> ActionWritebackReconciliationWorkflow:
    return ActionWritebackReconciliationWorkflow(
        engine=cast(Any, _Engine()),
        policy=cast(Any, policy or _Policy()),
        action_repository=cast(Any, repository or _Repository()),
        object_indexing_service=cast(Any, object()),
        object_records_service=cast(Any, objects or _Objects(None)),
        ontology_service=cast(Any, _Ontology()),
        runtime_service=cast(Any, runtime or _Runtime()),
        external_writeback_adapter=cast(Any, external),
    )


def test_reconciliation_workflow_requires_remote_outcome_evidence() -> None:
    workflow = _workflow()
    ctx = RequestContext()

    assert workflow._resolve_remote_outcome("succeeded", "remote-1", None, "writeback-1", ctx) == (
        "succeeded",
        "remote-1",
    )
    with pytest.raises(ValidationFailed, match="requires an operator"):
        workflow._resolve_remote_outcome(None, None, None, "writeback-1", ctx)


def test_reconciliation_workflow_lookup_rejects_retryable_or_unlanded_writebacks() -> None:
    ctx = RequestContext()
    retryable = _workflow(
        _Repository(writeback=_writeback(status="retryable")),
        external=_ExternalAdapter(RemoteOutcome(RemoteOutcomeStatus.LANDED, "remote-1")),
    )
    with pytest.raises(ValidationFailed, match="retryable writeback"):
        retryable._lookup_remote_outcome("s3://bucket/key", "writeback-1", ctx)

    absent = _workflow(
        _Repository(writeback=_writeback()),
        external=_ExternalAdapter(RemoteOutcome(RemoteOutcomeStatus.ABSENT)),
    )
    with pytest.raises(ValidationFailed, match="did not find"):
        absent._lookup_remote_outcome("s3://bucket/key", "writeback-1", ctx)


def test_reconciliation_workflow_not_found_and_state_guards_fail_closed() -> None:
    ctx = RequestContext()
    workflow = _workflow()
    with pytest.raises(NotFound, match="writeback"):
        workflow._required_writeback(object(), ctx, "missing")
    with pytest.raises(NotFound, match="action run"):
        workflow._required_action_run(object(), ctx, "missing")
    with pytest.raises(ValidationFailed, match="not resolvable"):
        workflow._require_outcome_unknown(_writeback(status="retryable"), _action_run(status="retryable"))


def test_reconciliation_workflow_requires_matching_target_object_version() -> None:
    ctx = RequestContext()
    action_run = _action_run(status="outcome_unknown")

    missing = _workflow(objects=_Objects(None))
    with pytest.raises(NotFound, match="target object"):
        missing._commit_reconciled_action(object(), ctx, action_run)

    stale = _workflow(objects=_Objects({"object_version": 2}))
    with pytest.raises(ConflictDetected, match="object version conflict"):
        stale._commit_reconciled_action(object(), ctx, action_run)


def test_reconciliation_workflow_approval_text_and_empty_sensitive_policy() -> None:
    workflow = _workflow(policy=_Policy())

    assert _required_approval_text("reason", " reviewed ") == "reviewed"
    with pytest.raises(ValidationFailed, match="field is required"):
        _required_approval_text("reason", " ")
    assert workflow._operator_approval_reason(RequestContext(), _writeback()) is None


class _SequentialRepository(_Repository):
    def __init__(self, writebacks: list[ActionWritebackRecord], action_run: ActionRunRow) -> None:
        super().__init__(action_run=action_run)
        self.writebacks = writebacks

    def action_writeback_by_id(self, **_kwargs: object) -> ActionWritebackRecord | None:
        return self.writebacks.pop(0)


def test_reconciliation_concurrent_winner_returns_already_reconciled_result() -> None:
    repository = _SequentialRepository(
        [
            _writeback(status="retryable"),
            _writeback(
                status="reconciled",
                response={"last_observed_status": "succeeded", "remote_resource_id": "remote-1"},
            ),
        ],
        _action_run(status="retryable"),
    )

    result = _workflow(repository).reconcile(
        "writeback-1",
        remote_status="succeeded",
        remote_resource_id="remote-1",
        ctx=RequestContext(),
    )

    assert result["alreadyReconciled"] is True


def test_external_pending_recovery_skips_without_adapter_or_uri() -> None:
    ctx = RequestContext()
    run_without_uri = _action_run(status="external_pending")

    # No external adapter configured: the run cannot be HEADed, so it is skipped (never failed).
    no_adapter = _workflow(external=None)
    skipped_no_adapter = no_adapter._recover_one_external_pending(
        ctx, {**run_without_uri, "external_writeback_uri": "s3://bucket/key"}
    )

    # Adapter present but the run has no persisted write-ahead URI: skipped before any remote lookup.
    with_adapter = _workflow(external=_ExternalAdapter(RemoteOutcome(RemoteOutcomeStatus.LANDED, "remote-1")))
    skipped_no_uri = with_adapter._recover_one_external_pending(ctx, run_without_uri)

    assert skipped_no_adapter["decision"] == "skipped"
    assert skipped_no_adapter.get("reason") == "missing_external_writeback_adapter"
    assert skipped_no_uri["decision"] == "skipped"
    assert skipped_no_uri.get("reason") == "missing_external_writeback_uri"


def test_reconciliation_approval_rejects_writeback_without_sensitive_parameters() -> None:
    workflow = _workflow(
        _Repository(writeback=_writeback(), action_run=_action_run(status="outcome_unknown")),
        policy=_Policy(),
    )

    with pytest.raises(ValidationFailed, match="does not require operator approval"):
        workflow._approval_required_writeback(RequestContext(), "writeback-1")


def test_reconciliation_permission_denial_is_audited_before_reraising() -> None:
    runtime = _Runtime()
    workflow = _workflow(policy=_Policy(is_denied=True), runtime=runtime)

    with pytest.raises(PermissionDenied):
        workflow._require_operations_retry(RequestContext(), "writeback-1")

    assert runtime.audits[0]["event_type"] == "permission.denied"


def _writeback(response: dict[str, object] | None = None, status: str = "outcome_unknown") -> ActionWritebackRecord:
    return ActionWritebackRecord(
        writeback_id="writeback-1",
        tenant_id="tenant-demo",
        action_run_id="run-1",
        mode="before_commit",
        connector_id="crm",
        request={"customerId": "customer-1"},
        response=response,
        status=status,
        idempotency_key="idem-1",
        attempts=1,
        created_at="2026-06-19T00:00:00Z",
        completed_at=None,
    )


def _action_run(status: str) -> ActionRunRow:
    return {
        "id": "run-1",
        "tenant_id": "tenant-demo",
        "action_type_id": "atype-1",
        "action_type_api_name": "ApproveOrder",
        "actor_user_id": "user-1",
        "target_object_type_id": "otype-1",
        "target_object_type_api_name": "Order",
        "target_object_id": "O-1001",
        "expected_object_version": 1,
        "parameters": {},
        "status": status,
        "idempotency_key": "idem-1",
        "request_fingerprint": "hash-1",
        "result": None,
        "error": None,
        "external_writeback_uri": None,
        "created_at": "2026-06-19T00:00:00Z",
        "completed_at": None,
    }
