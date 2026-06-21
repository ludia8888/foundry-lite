from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import pytest
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import AuditEventRecord, OutboxEventRecord
from foundry_lite.application.ports.action_repository import (
    ActionRunRecord,
    ActionRunRow,
    ActionWritebackRecord,
    ObjectEditRecord,
    ObjectTargetUpdate,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalSystemError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from tests.conftest import DEMO_ROOT, prepare_indexed_demo


class _InjectedActionCommitFailure(RuntimeError):
    pass


class _FailingActionRepository:
    def __init__(self, delegate: Any, *, fail_method: str) -> None:
        self.delegate = delegate
        self.fail_method = fail_method
        self.enabled = True

    def __getattr__(self, name: str) -> object:
        return getattr(self.delegate, name)

    def insert_action_run_or_get_existing(self, *, transaction: Any, record: ActionRunRecord) -> ActionRunRow | None:
        return self.delegate.insert_action_run_or_get_existing(transaction=transaction, record=record)

    def insert_action_writeback(self, *, transaction: Any, record: ActionWritebackRecord) -> None:
        self.delegate.insert_action_writeback(transaction=transaction, record=record)

    def update_object_target(self, *, transaction: Any, record: ObjectTargetUpdate) -> bool:
        if self.enabled and self.fail_method == "update_object_target_conflict":
            return False
        return self.delegate.update_object_target(transaction=transaction, record=record)

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        if self.enabled and self.fail_method == "insert_object_edit":
            raise _InjectedActionCommitFailure("injected object edit failure")
        self.delegate.insert_object_edit(transaction=transaction, record=record)

    def update_action_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
        status: str,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> None:
        if self.enabled and self.fail_method == "update_action_run_terminal" and status == "succeeded":
            raise _InjectedActionCommitFailure("injected action terminal failure")
        self.delegate.update_action_run_terminal(
            transaction=transaction,
            tenant_id=tenant_id,
            action_run_id=action_run_id,
            status=status,
            error=error,
            completed_at=completed_at,
        )


class _FailingRuntimeRepository:
    def __init__(self, delegate: Any, *, fail_audit: str | None = None, fail_outbox: str | None = None) -> None:
        self.delegate = delegate
        self.fail_audit = fail_audit
        self.fail_outbox = fail_outbox
        self.enabled = True

    def __getattr__(self, name: str) -> object:
        return getattr(self.delegate, name)

    def insert_audit_event(self, *, transaction: Any, record: AuditEventRecord) -> None:
        if self.enabled and record.event_type == self.fail_audit:
            raise _InjectedActionCommitFailure("injected action audit failure")
        self.delegate.insert_audit_event(transaction=transaction, record=record)

    def insert_outbox_event(self, *, transaction: Any, record: OutboxEventRecord) -> bool:
        if self.enabled and record.event_type == self.fail_outbox:
            raise _InjectedActionCommitFailure("injected action outbox failure")
        return self.delegate.insert_outbox_event(transaction=transaction, record=record)


def _core_with_action_failure(tmp_path: Path, fail_point: str) -> tuple[FoundryLite, object]:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    if fail_point.startswith("action:"):
        repository = _FailingActionRepository(dependencies.action_repository, fail_method=fail_point.split(":", 1)[1])
        return FoundryLite(dependencies=replace(dependencies, action_repository=repository)), repository
    repository = _runtime_failure_repository(dependencies, fail_point)
    return FoundryLite(dependencies=replace(dependencies, runtime_repository=repository)), repository


def _runtime_failure_repository(dependencies: CoreDependencies, fail_point: str) -> _FailingRuntimeRepository:
    if fail_point.startswith("audit:"):
        return _FailingRuntimeRepository(dependencies.runtime_repository, fail_audit=fail_point.split(":", 1)[1])
    return _FailingRuntimeRepository(dependencies.runtime_repository, fail_outbox=fail_point.split(":", 1)[1])


def _disable_injected_failure(repository: object) -> None:
    assert isinstance(repository, _FailingActionRepository | _FailingRuntimeRepository)
    repository.enabled = False


def _action_row_count(runs: Mapping[str, list[Mapping[str, object]]], idempotency_key: str) -> int:
    return sum(row.get("idempotency_key") == idempotency_key for row in runs["actionRuns"])


def _rows_for_key(
    runs: Mapping[str, list[Mapping[str, object]]],
    collection: str,
    idempotency_key: str,
) -> list[Mapping[str, object]]:
    return [row for row in runs[collection] if row.get("idempotency_key") == idempotency_key]


def _action_commit_evidence_counts(runs: Mapping[str, list[Mapping[str, object]]]) -> dict[str, int]:
    return {
        "writebacks": len(runs["actionWritebacks"]),
        "edits": len(runs["objectEdits"]),
        "audit_committed": _event_count(runs["auditEvents"], "action.run.committed"),
        "outbox_action": _event_count(runs["outboxEvents"], "action.run.committed"),
        "outbox_edit": _event_count(runs["outboxEvents"], "object.edit.committed"),
    }


def _event_count(rows: list[Mapping[str, object]], event_type: str) -> int:
    return sum(row.get("event_type") == event_type for row in rows)


@pytest.mark.integration_scenario("ontology_index")
def test_ontology_import_indexes_order_customer_and_supports_object_query(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)

    order = foundry.objects.get("Order", "O-1001", ctx=ctx, include_explain=True)
    customer = foundry.objects.get("Customer", "C-100", ctx=ctx)
    linked_customer = foundry.objects.links("Order", "O-1001", "OrderCustomer")[0]["to"]

    assert order["properties"]["orderId"] == "O-1001"
    assert order["sourceDatasetVersionId"]
    explain = order.get("explain")
    assert explain is not None
    assert explain["lineage"]
    assert customer["properties"]["customerId"] == "C-100"
    assert linked_customer["objectType"] == "Customer"
    assert linked_customer["objectId"] == "C-100"
    assert linked_customer["properties"]["customerId"] == "C-100"


def test_ontology_activation_rejects_missing_backing_column(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    bad_yaml = tmp_path / "bad-ontology.yaml"
    bad_yaml.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - apiName: orderId
        column: order_id
        type: string
        nullable: false
      - apiName: badProperty
        column: does_not_exist
        type: string
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed):
        foundry.ontology.apply(bad_yaml, ctx=ctx)


def test_ontology_migration_apply_blocks_required_action_parameter(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    candidate = tmp_path / "breaking-ontology.yaml"
    parameter_block = "      - apiName: reason\n        type: string\n        required: true"
    candidate.write_text(
        (DEMO_ROOT / "ontology" / "order-customer.yaml")
        .read_text(encoding="utf-8")
        .replace(
            parameter_block,
            f"{parameter_block}\n      - apiName: approvalCode\n        type: string\n        required: true",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed, match="ontology migration requires explicit migration plan") as exc_info:
        foundry.ontology.apply(candidate, ctx=ctx)

    plan = cast(dict[str, object], exc_info.value.details["ontologyMigration"])
    blocked = cast(list[dict[str, object]], plan["blockedChanges"])
    assert plan["status"] == "blocked"
    assert plan["sdkCompatibility"] == "major_version_required"
    assert {change["kind"] for change in blocked} == {"required_action_parameter_added"}


@pytest.mark.integration_scenario("object_action_audit")
def test_action_apply_is_idempotent_and_rejects_stale_object_version(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    first = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=ctx,
    )
    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=ctx,
    )

    approved = foundry.objects.get("Order", "O-1001", ctx=ctx)
    runs = foundry.operations.list_runs(ctx=ctx)
    assert approved["properties"]["status"] == "APPROVED"
    assert approved["properties"]["operatorNote"] == "Inventory confirmed"
    assert any(
        event["event_type"] == "object.edit.committed" and event["aggregate_id"] == "O-1001"
        for event in runs["outboxEvents"]
    )
    assert any(
        event["event_type"] == "action.run.committed" and event["resource_id"] == first["actionRunId"]
        for event in runs["auditEvents"]
    )

    assert replay["idempotentReplay"] is True
    assert replay["actionRunId"] == first["actionRunId"]
    with pytest.raises(ConflictDetected) as idempotency_conflict:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Different body with reused key"},
            idempotency_key="same-key",
            ctx=ctx,
        )
    conflict_runs = foundry.operations.list_runs(ctx=ctx)
    assert idempotency_conflict.value.details["action_run_id"] == first["actionRunId"]
    assert any(
        event["event_type"] == "action.run.idempotency_conflict" and event["resource_id"] == first["actionRunId"]
        for event in conflict_runs["auditEvents"]
    )
    with pytest.raises(ConflictDetected):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed again"},
            idempotency_key="different-key",
            ctx=ctx,
        )


def test_action_same_idempotency_key_different_body_returns_409(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    first = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key-different-body",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected) as exc_info:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Different body"},
            idempotency_key="same-key-different-body",
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    assert exc_info.value.details["action_run_id"] == first["actionRunId"]
    assert any(
        event["event_type"] == "action.run.idempotency_conflict" and event["resource_id"] == first["actionRunId"]
        for event in runs["auditEvents"]
    )


def test_action_precondition_stale_read_conflicts_on_commit(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "First writer"},
        idempotency_key="first-writer",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Stale writer"},
            idempotency_key="stale-writer",
            ctx=ctx,
        )


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_before_commit_writeback_failure_does_not_edit_object(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    with pytest.raises(ExternalSystemError):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="writeback-fails",
            simulate_writeback_failure=True,
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    failed_runs = [
        run
        for run in foundry.operations.list_runs(ctx=ctx)["actionRuns"]
        if run["idempotency_key"] == "writeback-fails"
    ]
    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="writeback-fails",
        ctx=ctx,
    )
    after_replay_runs = [
        run
        for run in foundry.operations.list_runs(ctx=ctx)["actionRuns"]
        if run["idempotency_key"] == "writeback-fails"
    ]
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert len(failed_runs) == 1
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "failed"
    assert replay["actionRunId"] == failed_runs[0]["id"]
    assert [run["id"] for run in after_replay_runs] == [failed_runs[0]["id"]]
    writeback = foundry.operations.list_runs(ctx=ctx)["actionWritebacks"][0]
    writeback_request = cast(Mapping[str, object], writeback["request"])
    writeback_response = cast(Mapping[str, object], writeback["response"])
    assert writeback["connector_id"] == "mock_erp_simulator"
    assert writeback_request["networkCall"] is False
    assert writeback_response["simulated"] is True
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "action.run.failed" for event in audit_events)


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_external_success_response_lost_becomes_outcome_unknown(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-response-lost"

    with pytest.raises(ExternalOutcomeUnknown) as raised:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    unknown_runs = _rows_for_key(snapshot, "actionRuns", idempotency_key)
    writebacks = _rows_for_key(snapshot, "actionWritebacks", idempotency_key)
    writeback_request = cast(Mapping[str, object], writebacks[0]["request"])
    writeback_response = cast(Mapping[str, object], writebacks[0]["response"])
    error = cast(Mapping[str, object], unknown_runs[0]["error"])
    error_details = cast(Mapping[str, object], error["details"])

    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert raised.value.details["state"] == "OUTCOME_UNKNOWN"
    assert len(unknown_runs) == 1
    assert unknown_runs[0]["status"] == "outcome_unknown"
    assert error["type"] == "EXTERNAL_OUTCOME_UNKNOWN"
    assert error_details["last_observed_status"] == "unknown"
    assert len(writebacks) == 1
    assert writebacks[0]["status"] == "outcome_unknown"
    assert writebacks[0]["attempts"] == 1
    assert writeback_request["idempotency_key"] == idempotency_key
    assert writeback_request["request_hash"] == unknown_runs[0]["request_fingerprint"]
    assert writeback_request["networkCall"] is False
    assert writeback_response["outcome_unknown"] is True
    assert writeback_response["external_operation_id"] == f"mock-op-{idempotency_key}"
    assert writeback_response["last_observed_status"] == "unknown"
    assert writeback_response["remote_resource_id"] is None
    assert "reconciliation_deadline" in writeback_response
    assert any(event["event_type"] == "action.run.outcome_unknown" for event in snapshot["auditEvents"])


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_outcome_unknown_is_not_blindly_retried(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-outcome-unknown-replay"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )
    first_snapshot = foundry.operations.list_runs(ctx=ctx)
    first_run = _rows_for_key(first_snapshot, "actionRuns", idempotency_key)[0]

    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key=idempotency_key,
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    replay_snapshot = foundry.operations.list_runs(ctx=ctx)
    replay_runs = _rows_for_key(replay_snapshot, "actionRuns", idempotency_key)
    replay_writebacks = _rows_for_key(replay_snapshot, "actionWritebacks", idempotency_key)
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "outcome_unknown"
    assert replay["actionRunId"] == first_run["id"]
    assert [run["id"] for run in replay_runs] == [first_run["id"]]
    assert len(replay_writebacks) == 1


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_reconciliation_resolves_remote_success(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-reconcile-success"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP success confirmed later"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )
    writeback_id = _rows_for_key(foundry.operations.list_runs(ctx=ctx), "actionWritebacks", idempotency_key)[0]["id"]

    result = foundry.operations.reconcile_action_writeback(
        cast(str, writeback_id),
        remote_status="succeeded",
        remote_resource_id=f"mock-resource-{idempotency_key}",
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    reconciled_runs = _rows_for_key(snapshot, "actionRuns", idempotency_key)
    writebacks = _rows_for_key(snapshot, "actionWritebacks", idempotency_key)
    response = cast(Mapping[str, object], writebacks[0]["response"])
    assert result["status"] == "reconciled"
    assert result["remoteStatus"] == "succeeded"
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["status"] == "APPROVED"
    assert reconciled_runs[0]["status"] == "reconciled"
    assert reconciled_runs[0]["error"] is None
    assert writebacks[0]["status"] == "reconciled"
    assert response["reconciled"] is True
    assert response["remote_resource_id"] == f"mock-resource-{idempotency_key}"
    assert response["last_observed_status"] == "succeeded"
    assert _event_count(snapshot["auditEvents"], "action.run.reconciled") == 1


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_concurrent_reconciliation_has_one_winner(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-reconcile-race"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP race confirmation"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )
    writeback_id = cast(
        str, _rows_for_key(foundry.operations.list_runs(ctx=ctx), "actionWritebacks", idempotency_key)[0]["id"]
    )
    start = Barrier(2)

    def reconcile() -> Mapping[str, object]:
        start.wait()
        return foundry.operations.reconcile_action_writeback(
            writeback_id,
            remote_status="succeeded",
            remote_resource_id=f"mock-resource-{idempotency_key}",
            ctx=ctx,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reconcile(), range(2)))

    snapshot = foundry.operations.list_runs(ctx=ctx)
    assert sum("objectEditId" in result for result in results) == 1
    assert sum(result.get("alreadyReconciled") is True for result in results) == 1
    assert _event_count(snapshot["auditEvents"], "action.run.reconciled") == 1
    assert len(_rows_for_key(snapshot, "objectEdits", idempotency_key)) == 1


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_sensitive_writeback_payload_is_masked_in_audit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _margin_action_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-sensitive-reconcile"
    sensitive_margin = 9999.99

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "AdjustMargin",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"margin": sensitive_margin},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )
    pending_snapshot = foundry.operations.list_runs(ctx=ctx)
    writeback_id = cast(str, _rows_for_key(pending_snapshot, "actionWritebacks", idempotency_key)[0]["id"])
    pending_run = _rows_for_key(pending_snapshot, "actionRuns", idempotency_key)[0]
    pending_audit = next(
        event for event in pending_snapshot["auditEvents"] if event["event_type"] == "action.run.outcome_unknown"
    )

    assert cast(Mapping[str, object], pending_run["parameters"])["margin"] == "***MASKED***"
    assert str(sensitive_margin) not in repr(pending_audit)
    assert str(sensitive_margin) not in repr(_rows_for_key(pending_snapshot, "actionWritebacks", idempotency_key)[0])

    result = foundry.operations.reconcile_action_writeback(
        writeback_id,
        remote_status="succeeded",
        remote_resource_id=f"mock-resource-{idempotency_key}",
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    action_run_id = result["actionRunId"]
    reconciled_run = _rows_for_key(snapshot, "actionRuns", idempotency_key)[0]
    committed_audit = next(
        event
        for event in snapshot["auditEvents"]
        if event["event_type"] == "action.run.committed" and event["resource_id"] == action_run_id
    )
    reconciled_audit = next(
        event
        for event in snapshot["auditEvents"]
        if event["event_type"] == "action.run.reconciled" and event["resource_id"] == action_run_id
    )

    assert after["properties"]["margin"] == sensitive_margin
    assert reconciled_run["status"] == "reconciled"
    assert cast(Mapping[str, object], reconciled_run["parameters"])["margin"] == "***MASKED***"
    assert committed_audit["before_ref"]["margin"] == "***MASKED***"
    assert committed_audit["after_ref"]["patch"]["margin"] == "***MASKED***"
    assert str(sensitive_margin) not in repr(reconciled_audit)


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_external_success_local_failure_requires_compensation(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-local-commit-fails"

    with pytest.raises(ExternalCompensationRequired) as raised:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP accepted before local commit"},
            idempotency_key=idempotency_key,
            simulate_writeback_compensation_required=True,
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    compensation_runs = _rows_for_key(snapshot, "actionRuns", idempotency_key)
    writebacks = _rows_for_key(snapshot, "actionWritebacks", idempotency_key)
    writeback_request = cast(Mapping[str, object], writebacks[0]["request"])
    writeback_response = cast(Mapping[str, object], writebacks[0]["response"])
    error = cast(Mapping[str, object], compensation_runs[0]["error"])
    error_details = cast(Mapping[str, object], error["details"])

    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert raised.value.details["state"] == "COMPENSATION_REQUIRED"
    assert compensation_runs[0]["status"] == "compensation_required"
    assert error["type"] == "EXTERNAL_COMPENSATION_REQUIRED"
    assert error_details["last_observed_status"] == "succeeded"
    assert error_details["compensation_action_type"] == "mock_reverse_writeback"
    assert len(writebacks) == 1
    assert writebacks[0]["status"] == "compensation_required"
    assert writebacks[0]["attempts"] == 1
    assert writeback_request["idempotency_key"] == idempotency_key
    assert writeback_request["request_hash"] == compensation_runs[0]["request_fingerprint"]
    assert writeback_response["compensation_required"] is True
    assert writeback_response["external_operation_id"] == f"mock-op-{idempotency_key}"
    assert writeback_response["remote_resource_id"] == f"mock-resource-{idempotency_key}"
    assert writeback_response["last_observed_status"] == "succeeded"
    assert writeback_response["compensation_action_type"] == "mock_reverse_writeback"
    assert any(event["event_type"] == "action.run.compensation_required" for event in snapshot["auditEvents"])


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_compensation_is_idempotent(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-compensation-replay"

    with pytest.raises(ExternalCompensationRequired):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP accepted before local commit"},
            idempotency_key=idempotency_key,
            simulate_writeback_compensation_required=True,
            ctx=ctx,
        )
    first_snapshot = foundry.operations.list_runs(ctx=ctx)
    first_run = _rows_for_key(first_snapshot, "actionRuns", idempotency_key)[0]

    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "ERP accepted before local commit"},
        idempotency_key=idempotency_key,
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    replay_snapshot = foundry.operations.list_runs(ctx=ctx)
    replay_runs = _rows_for_key(replay_snapshot, "actionRuns", idempotency_key)
    replay_writebacks = _rows_for_key(replay_snapshot, "actionWritebacks", idempotency_key)
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "compensation_required"
    assert replay["actionRunId"] == first_run["id"]
    assert [run["id"] for run in replay_runs] == [first_run["id"]]
    assert len(replay_writebacks) == 1


@pytest.mark.parametrize(
    "fail_point",
    [
        "action:insert_object_edit",
        "action:update_action_run_terminal",
        "outbox:object.edit.committed",
        "audit:action.run.committed",
    ],
)
def test_action_commit_object_edit_audit_outbox_atomic(tmp_path: Path, fail_point: str) -> None:
    foundry, failing_repository = _core_with_action_failure(tmp_path, fail_point)
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    before = foundry.operations.list_runs(ctx=ctx)

    with pytest.raises(_InjectedActionCommitFailure):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Atomicity proof"},
            idempotency_key="atomicity-proof",
            ctx=ctx,
        )

    after_failure = foundry.operations.list_runs(ctx=ctx)
    unchanged = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert unchanged["objectVersion"] == order["objectVersion"]
    assert unchanged["properties"]["status"] == "PENDING"
    assert _action_row_count(after_failure, "atomicity-proof") == 0
    assert _action_commit_evidence_counts(after_failure) == _action_commit_evidence_counts(before)

    _disable_injected_failure(failing_repository)
    retry = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Atomicity proof"},
        idempotency_key="atomicity-proof",
        ctx=ctx,
    )
    assert retry["status"] == "succeeded"


def test_action_commit_concurrency_conflict_leaves_failed_run_evidence(tmp_path: Path) -> None:
    foundry, _failing_repository = _core_with_action_failure(tmp_path, "action:update_object_target_conflict")
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    with pytest.raises(ConflictDetected):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Concurrent writer lost"},
            idempotency_key="commit-conflict-evidence",
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    conflict_runs = _rows_for_key(runs, "actionRuns", "commit-conflict-evidence")
    assert len(conflict_runs) == 1
    assert conflict_runs[0]["status"] == "conflict"
    assert conflict_runs[0]["error"]["type"] == "CONFLICT"
    assert _rows_for_key(runs, "actionWritebacks", "commit-conflict-evidence") == []
    assert _rows_for_key(runs, "objectEdits", "commit-conflict-evidence") == []
    assert any(
        event["event_type"] == "action.run.failed" and event["resource_id"] == conflict_runs[0]["id"]
        for event in runs["auditEvents"]
    )


def test_outbox_event_not_published_before_domain_commit(tmp_path: Path) -> None:
    # The action emits outbox events through the runtime outbox table inside the
    # same domain transaction; there is no direct external publish call. Injecting
    # a failure on the second outbox insert rolls the whole commit back, so the
    # earlier action.run.committed outbox row inserted in the same transaction
    # must also vanish: an outbox event is never durable (and therefore never
    # publishable by the outbox drain path) before the domain commit succeeds.
    foundry, failing_repository = _core_with_action_failure(tmp_path, "outbox:object.edit.committed")
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    before = _action_commit_evidence_counts(foundry.operations.list_runs(ctx=ctx))

    with pytest.raises(_InjectedActionCommitFailure):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Outbox-before-commit proof"},
            idempotency_key="outbox-before-commit",
            ctx=ctx,
        )

    after_failure = foundry.operations.list_runs(ctx=ctx)
    assert _action_commit_evidence_counts(after_failure) == before
    assert _event_count(after_failure["outboxEvents"], "action.run.committed") == before["outbox_action"]
    assert _event_count(after_failure["outboxEvents"], "object.edit.committed") == before["outbox_edit"]

    _disable_injected_failure(failing_repository)
    committed = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Outbox-before-commit proof"},
        idempotency_key="outbox-before-commit",
        ctx=ctx,
    )
    after_commit = _action_commit_evidence_counts(foundry.operations.list_runs(ctx=ctx))

    # Only after the domain commit succeeds do the outbox rows appear.
    assert committed["status"] == "succeeded"
    assert after_commit["outbox_action"] == before["outbox_action"] + 1
    assert after_commit["outbox_edit"] == before["outbox_edit"] + 1


def test_action_audit_masks_sensitive_params(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _margin_action_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    result = foundry.actions.apply(
        "AdjustMargin",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"margin": 9999.99},
        idempotency_key="audit-mask-margin",
        ctx=ctx,
    )

    changed = foundry.objects.get("Order", "O-1001", ctx=ctx)
    audit = next(
        event
        for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]
        if event["event_type"] == "action.run.committed" and event["resource_id"] == result["actionRunId"]
    )
    assert changed["properties"]["margin"] == 9999.99
    assert result["patch"]["margin"] == 9999.99
    assert audit["before_ref"]["margin"] == "***MASKED***"
    assert audit["after_ref"]["patch"]["margin"] == "***MASKED***"


def test_operations_read_is_operator_only_and_redacts_sensitive_params(foundry: FoundryLite, tmp_path: Path) -> None:
    admin_ctx = _prepare_demo_with_ontology(foundry, _margin_action_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=admin_ctx)
    result = foundry.actions.apply(
        "AdjustMargin",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"margin": 9999.99},
        idempotency_key="ops-redact-margin",
        ctx=admin_ctx,
    )
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    operator = RequestContext(actor_user_id="de-1", roles=("data_engineer",))

    # A viewer must not reach operations at all — summary, page, or detail.
    with pytest.raises(PermissionDenied):
        foundry.operations.list_runs(ctx=viewer)
    with pytest.raises(PermissionDenied):
        foundry.operations.query_runs(ctx=viewer)
    with pytest.raises(PermissionDenied):
        foundry.operations.run_detail("action", result["actionRunId"], ctx=viewer)

    # An operator may read, but the sensitive action parameter that audit masks
    # must not leak raw through the operations surface either.
    detail = foundry.operations.run_detail("action", result["actionRunId"], ctx=operator)
    assert detail["row"]["parameters"]["margin"] == "***MASKED***"
    snapshot = foundry.operations.list_runs(ctx=operator)
    margin_runs = [
        run
        for run in snapshot["actionRuns"]
        if isinstance(run.get("parameters"), dict) and "margin" in run["parameters"]
    ]
    assert margin_runs
    assert all(run["parameters"]["margin"] == "***MASKED***" for run in margin_runs)


@pytest.mark.integration_scenario("permission_tenant_isolation")
def test_viewer_sees_masked_margin_and_cannot_approve_order(
    foundry: FoundryLite,
) -> None:
    admin_ctx = prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    finance = RequestContext(actor_user_id="finance-1", roles=("finance",))
    other_tenant = RequestContext(
        tenant_id="tenant-other",
        actor_user_id="other-admin",
        roles=("admin", "data_engineer", "ops_manager", "finance"),
    )
    order = foundry.objects.get("Order", "O-1001", ctx=viewer)
    dataset_preview = foundry.datasets.preview("clean.orders", ctx=viewer, limit=1)
    finance_order = foundry.objects.get("Order", "O-1001", ctx=finance)
    finance_query = foundry.objects.query(
        "Order",
        ctx=finance,
        filter_ast={"property": "margin", "op": "gte", "value": 80.0},
        order_by=[{"property": "margin", "direction": "desc"}],
        limit=2,
    )

    assert order["properties"]["margin"] == "***MASKED***"
    assert dataset_preview[0]["order_id"] == "O-1001"
    assert finance_order["properties"]["margin"] != "***MASKED***"
    assert [item["objectId"] for item in finance_query["items"]] == ["O-1001", "O-1002"]
    with pytest.raises(ValidationFailed, match="masked property"):
        foundry.objects.query(
            "Order",
            ctx=viewer,
            filter_ast={"property": "margin", "op": "gte", "value": 80.0},
        )
    with pytest.raises(ValidationFailed, match="masked property"):
        foundry.objects.query(
            "Order",
            ctx=viewer,
            order_by=[{"property": "margin", "direction": "desc"}],
        )
    with pytest.raises(ValidationFailed, match="masked property"):
        foundry.objects.create_set(
            "Masked Margin Orders",
            "Order",
            set_type="dynamic",
            filter_ast={"property": "margin", "op": "gte", "value": 80.0},
            ctx=viewer,
        )
    with pytest.raises(PermissionDenied):
        foundry.ontology.apply(str(DEMO_ROOT / "ontology" / "order-customer.yaml"), ctx=viewer)
    with pytest.raises(NotFound):
        foundry.objects.get("Order", "O-1001", ctx=other_tenant)
    assert all(not rows for rows in foundry.operations.list_runs(ctx=other_tenant).values())
    with pytest.raises(PermissionDenied):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="viewer-denied",
            ctx=viewer,
        )
    assert any(event["decision"] == "deny" for event in foundry.operations.list_runs(ctx=admin_ctx)["auditEvents"])


@pytest.mark.integration_scenario("permission_tenant_isolation")
def test_dataset_preview_masks_sensitive_columns_for_unprivileged_roles(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    finance = RequestContext(actor_user_id="finance-1", roles=("finance",))

    # The backing dataset must not leak the same value that Object masking hides:
    # preview applies the same role-aware column classification.
    viewer_rows = foundry.datasets.preview("clean.orders", ctx=viewer, limit=3)
    finance_rows = foundry.datasets.preview("clean.orders", ctx=finance, limit=3)

    assert viewer_rows
    assert all(row["margin"] == "***MASKED***" for row in viewer_rows)
    assert any(row["margin"] != "***MASKED***" for row in finance_rows)
    # non-sensitive columns stay visible to the viewer
    assert viewer_rows[0]["order_id"] == "O-1001"


@pytest.mark.integration_scenario("permission_tenant_isolation")
def test_explain_does_not_bypass_property_masking(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    finance = RequestContext(actor_user_id="finance-1", roles=("finance",))
    admin = RequestContext(actor_user_id="admin-1", roles=("admin",))

    # A viewer without object:explain must not receive the explain block at all
    # (it carries base/edit properties plus operational lineage/source metadata).
    viewer_order = foundry.objects.get("Order", "O-1001", ctx=viewer, include_explain=True)
    assert viewer_order["properties"]["margin"] == "***MASKED***"
    assert "explain" not in viewer_order

    # An operator may explain, but the masked value must not leak via base/edit either.
    ops_order = foundry.objects.get("Order", "O-1001", ctx=ops_manager, include_explain=True)
    ops_explain = ops_order["explain"]
    assert ops_explain is not None
    assert ops_explain["baseProperties"]["margin"] == "***MASKED***"
    assert ops_explain["editProperties"].get("margin", "***MASKED***") == "***MASKED***"
    assert ops_explain["lineage"]

    # finance/admin see the real value in explain, consistent with masked-properties policy.
    for privileged in (finance, admin):
        privileged_order = foundry.objects.get("Order", "O-1001", ctx=privileged, include_explain=True)
        assert privileged_order["explain"]["baseProperties"]["margin"] != "***MASKED***"


@pytest.mark.integration_scenario("permission_tenant_isolation")
def test_object_property_lineage_resolves_to_pinned_dataset_version(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    finance = RequestContext(actor_user_id="finance-1", roles=("finance",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))

    order = foundry.objects.get("Order", "O-1001", ctx=finance, include_explain=True)
    explain = order["explain"]
    property_lineage = {item["propertyName"]: item for item in explain["propertyLineage"]}

    status = property_lineage["status"]
    assert status["sourceDatasetVersionId"] == order["sourceDatasetVersionId"]
    assert status["sourceObjectVersion"] == order["objectVersion"]
    assert status["sourceColumn"] == "source_status"
    assert status["valueSource"] == "dataset_column"
    assert status["propertyVersion"] == 1

    ops_order = foundry.objects.get("Order", "O-1001", ctx=ops_manager, include_explain=True)
    margin = {item["propertyName"]: item for item in ops_order["explain"]["propertyLineage"]}["margin"]
    assert margin["maskingStatus"] == "masked"
    raw_margin = str(order["properties"]["margin"])
    leak_checked_payload = {
        key: value for key, value in margin.items() if key not in {"sourceDatasetVersionId", "sourceHash"}
    }
    assert raw_margin not in repr(leak_checked_payload)


def test_only_ops_manager_or_admin_can_execute_approve_order(
    foundry: FoundryLite,
) -> None:
    prepare_indexed_demo(foundry)
    data_engineer = RequestContext(actor_user_id="engineer-1", roles=("data_engineer",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    order = foundry.objects.get("Order", "O-1001", ctx=ops_manager)

    with pytest.raises(PermissionDenied):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="engineer-denied",
            ctx=data_engineer,
        )

    approved = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="ops-approved",
        ctx=ops_manager,
    )

    assert approved["status"] == "succeeded"
    assert any(event["decision"] == "deny" for event in foundry.operations.list_runs(ctx=ops_manager)["auditEvents"])


def test_action_rejects_target_object_type_mismatch_before_side_effects(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    ctx = demo_admin_context()
    idempotency_key = "action-target-mismatch"

    with pytest.raises(ValidationFailed, match="target object type mismatch"):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Customer",
            object_id="C-100",
            expected_object_version=1,
            params={"reason": "Inventory confirmed"},
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    assert _action_row_count(runs, idempotency_key) == 0
    assert _rows_for_key(runs, "actionWritebacks", idempotency_key) == []
    assert _rows_for_key(runs, "objectEdits", idempotency_key) == []
    assert _rows_for_key(runs, "outboxEvents", idempotency_key) == []


def _prepare_demo_with_ontology(foundry: FoundryLite, ontology_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_customers", ctx=ctx)
    foundry.ontology.apply(str(ontology_path), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    return ctx


def _margin_action_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    ontology = ontology.replace(
        "      - apiName: margin\n        column: margin\n        type: float\n        classification: finance",
        "      - apiName: margin\n"
        "        column: margin\n"
        "        type: float\n"
        "        classification: finance\n"
        "        editable: true\n"
        "        editPolicy: edit_wins",
    )
    ontology = f"""{ontology}
  - apiName: AdjustMargin
    displayName: Adjust margin
    target: Order
    parameters:
      - apiName: margin
        type: float
        required: true
    mutations:
      - type: setProperty
        property: margin
        valueFrom: params.margin
"""
    path = tmp_path / "order-customer-margin-action.yaml"
    path.write_text(ontology, encoding="utf-8")
    return path
