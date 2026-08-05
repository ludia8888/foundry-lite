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
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackPayload,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
    WriteReceipt,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalRetryableWriteback,
    ExternalSystemError,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import select, update

from tests.conftest import DEMO_ROOT, prepare_indexed_demo


class _InjectedActionCommitFailure(RuntimeError):
    pass


class _MemoryExternalWritebackAdapter:
    profile_name = "memory-external-writeback"

    def __init__(self, write_status: RemoteOutcomeStatus) -> None:
        self.write_status = write_status
        self.lookup_count = 0

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del target, payload
        if self.write_status is RemoteOutcomeStatus.AMBIGUOUS:
            return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS)
        return WriteReceipt(status=RemoteOutcomeStatus.LANDED, remote_resource_id="remote-sensitive-writeback")

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        del target
        self.lookup_count += 1
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id="remote-sensitive-writeback")


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
        transition: StatusTransition,
        error: Mapping[str, object] | None,
        completed_at: str | None,
        result: Mapping[str, object] | None = None,
    ) -> bool:
        if self.enabled and self.fail_method == "update_action_run_terminal" and transition.to_status == "succeeded":
            raise _InjectedActionCommitFailure("injected action terminal failure")
        return self.delegate.update_action_run_terminal(
            transaction=transaction,
            tenant_id=tenant_id,
            action_run_id=action_run_id,
            transition=transition,
            error=error,
            completed_at=completed_at,
            result=result,
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


def _runtime_rows(runs: Mapping[str, object], collection: str) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], runs[collection])


def _action_row_count(runs: Mapping[str, object], idempotency_key: str) -> int:
    return sum(row.get("idempotency_key") == idempotency_key for row in _runtime_rows(runs, "actionRuns"))


def _rows_for_key(
    runs: Mapping[str, object],
    collection: str,
    idempotency_key: str,
) -> list[Mapping[str, object]]:
    return [row for row in _runtime_rows(runs, collection) if row.get("idempotency_key") == idempotency_key]


def _action_commit_evidence_counts(runs: Mapping[str, object]) -> dict[str, int]:
    return {
        "writebacks": len(_runtime_rows(runs, "actionWritebacks")),
        "edits": len(_runtime_rows(runs, "objectEdits")),
        "audit_committed": _event_count(_runtime_rows(runs, "auditEvents"), "action.run.committed"),
        "outbox_action": _event_count(_runtime_rows(runs, "outboxEvents"), "action.run.committed"),
        "outbox_edit": _event_count(_runtime_rows(runs, "outboxEvents"), "object.edit.committed"),
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


def test_ontology_migration_reindex_executor_completes_required_object_reindex(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    assert foundry.objects.get("Order", "O-1001", ctx=ctx)["properties"]["status"] == "PENDING"

    foundry.ontology.apply(_status_remapped_ontology(tmp_path), ctx=ctx)
    catalog = foundry.ontology.catalog(ctx=ctx)
    order_type = next(item for item in catalog["objectTypes"] if item["apiName"] == "Order")
    reindex_key = str(order_type["config"]["objectReindexPlan"][0]["reindexKey"])

    assert order_type["config"]["servingRecordScope"] == "object_type_id"
    with pytest.raises(NotFound):
        foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert foundry.objects.query("Order", ctx=ctx)["items"] == []

    result = foundry.objects.reindex_ontology_migration("Order", reindex_key, ctx=ctx)
    replay = foundry.objects.reindex_ontology_migration("Order", reindex_key, ctx=ctx)
    completed_catalog = foundry.ontology.catalog(ctx=ctx)
    completed_order_type = next(item for item in completed_catalog["objectTypes"] if item["apiName"] == "Order")
    remapped = foundry.objects.get("Order", "O-1001", ctx=ctx)

    assert result["ontologyReindexKey"] == reindex_key
    assert result["servingContractStatus"] == "object_reindex_complete"
    assert result["changedFields"] == ["properties.status"]
    assert replay["index_run_id"] == result["index_run_id"]
    assert replay["isIdempotentReplay"] is True
    assert completed_order_type["config"]["servingContractStatus"] == "object_reindex_complete"
    assert completed_order_type["config"]["objectReindexCompleted"]["indexRunId"] == result["index_run_id"]
    assert remapped["properties"]["status"] == "C-100"


def test_ontology_mapping_migrates_with_dataset_schema(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    status_code_csv = _clean_orders_with_status_code(tmp_path)
    committed = foundry.datasets.upload_csv("clean.orders", status_code_csv, ctx=ctx)
    with foundry.engine.begin() as conn:
        tx_row = (
            conn.execute(
                select(db.dataset_transactions).where(db.dataset_transactions.c.id == committed.transaction_id)
            )
            .mappings()
            .one()
        )

    foundry.ontology.apply(_status_code_ontology(tmp_path), ctx=ctx)
    catalog = foundry.ontology.catalog(ctx=ctx)
    order_type = next(item for item in catalog["objectTypes"] if item["apiName"] == "Order")
    migration = next(
        event["after_ref"]["ontologyMigration"]
        for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]
        if event["event_type"] == "ontology.version.activated" and "ontologyMigration" in event["after_ref"]
    )
    warning = next(item for item in migration["warnings"] if item["kind"] == "property_mapping_changed")
    reindex_key = str(order_type["config"]["objectReindexPlan"][0]["reindexKey"])

    result = foundry.objects.reindex_ontology_migration("Order", reindex_key, ctx=ctx)
    migrated = foundry.objects.get("Order", "O-1001", ctx=ctx, include_explain=True)
    status_lineage = next(item for item in migrated["explain"]["propertyLineage"] if item["propertyName"] == "status")
    schema_changes = tx_row["metadata"]["schemaEvolution"]["changes"]

    assert any(change.get("column") == "status_code" for change in schema_changes)
    assert warning["details"] == {"previousColumn": "source_status", "nextColumn": "status_code"}
    assert result["changedFields"] == ["properties.status"]
    assert migrated["properties"]["status"] == "BACKORDERED"
    assert status_lineage["sourceColumn"] == "status_code"
    # Order is multi-datasource: the record's source id composes both segment
    # versions in declaration order, with the refreshed erp version first.
    assert str(status_lineage["sourceDatasetVersionId"]).split("+")[0] == committed.version_id


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
    assert replay["objectEditId"] == first["objectEditId"]
    assert replay["newObjectVersion"] == first["newObjectVersion"]
    assert replay["patch"] == first["patch"]
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
        simulate_writeback_failure=True,
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
def test_retryable_writeback_failure_is_visible_without_object_edit(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-retryable"

    with pytest.raises(ExternalRetryableWriteback) as raised:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "external system not changed"},
            idempotency_key=idempotency_key,
            simulate_writeback_retryable=True,
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    retryable_runs = _rows_for_key(snapshot, "actionRuns", idempotency_key)
    writebacks = _rows_for_key(snapshot, "actionWritebacks", idempotency_key)
    writeback_response = cast(Mapping[str, object], writebacks[0]["response"])
    error = cast(Mapping[str, object], retryable_runs[0]["error"])
    error_details = cast(Mapping[str, object], error["details"])

    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert raised.value.details["state"] == "RETRYABLE"
    assert retryable_runs[0]["status"] == "retryable"
    assert error["type"] == "EXTERNAL_RETRYABLE_WRITEBACK"
    assert error_details["external_system_changed"] is False
    assert error_details["last_observed_status"] == "not_changed"
    assert writebacks[0]["status"] == "retryable"
    assert writeback_response["retryable"] is True
    assert writeback_response["external_system_changed"] is False
    assert writeback_response["last_observed_status"] == "not_changed"
    assert writeback_response["retry_after_seconds"] == 60
    assert any(event["event_type"] == "action.run.retryable" for event in snapshot["auditEvents"])

    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "external system not changed"},
        idempotency_key=idempotency_key,
        simulate_writeback_retryable=True,
        ctx=ctx,
    )
    replay_snapshot = foundry.operations.list_runs(ctx=ctx)

    assert replay["idempotentReplay"] is True
    assert replay["status"] == "retryable"
    assert replay["actionRunId"] == retryable_runs[0]["id"]
    assert len(_rows_for_key(replay_snapshot, "actionWritebacks", idempotency_key)) == 1

    writeback_id = cast(str, writebacks[0]["id"])
    with pytest.raises(ValidationFailed, match="not approval-recoverable"):
        foundry.operations.approve_action_writeback_recovery(
            writeback_id,
            approval_id="approval-retryable-should-not-apply",
            reason="operator cannot release retryable external-not-changed rows",
            ctx=ctx,
        )
    rejected_snapshot = foundry.operations.list_runs(ctx=ctx)
    assert _event_count(rejected_snapshot["auditEvents"], "action.writeback.recovery_approved") == 0


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
        simulate_writeback_outcome_unknown=True,
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

    replay = foundry.operations.reconcile_action_writeback(
        cast(str, writeback_id),
        remote_status="succeeded",
        remote_resource_id="caller-supplied-different-remote",
        ctx=ctx,
    )
    assert replay["alreadyReconciled"] is True
    assert replay["remoteResourceId"] == f"mock-resource-{idempotency_key}"


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_reconciliation_uses_failed_run_action_definition_after_ontology_change(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-reconcile-old-action-definition"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "ERP success confirmed after action changed"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )
    writeback_id = cast(
        str,
        _rows_for_key(foundry.operations.list_runs(ctx=ctx), "actionWritebacks", idempotency_key)[0]["id"],
    )

    foundry.ontology.apply(_cancel_approve_action_ontology(tmp_path), ctx=ctx)
    result = foundry.operations.reconcile_action_writeback(
        writeback_id,
        remote_status="succeeded",
        remote_resource_id=f"mock-resource-{idempotency_key}",
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert result["status"] == "reconciled"
    assert after["properties"]["status"] == "APPROVED"
    assert after["properties"]["operatorNote"] == "ERP success confirmed after action changed"


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
def test_bounded_recovery_requires_operator_approval_for_sensitive_writeback(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _margin_action_ontology(tmp_path))
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.AMBIGUOUS)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-sensitive-recovery-approval"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "AdjustMargin",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"margin": 9999.99},
            idempotency_key=idempotency_key,
            external_writeback_uri="memory://erp/orders/O-1001/margin",
            ctx=ctx,
        )

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)
    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    item = result["items"][0]

    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert item["decision"] == "skipped"
    assert item.get("reason") == "operator_approval_required"
    assert item.get("approvalRequired") is True
    assert item.get("approvalReason") == "sensitive_action_parameter"
    assert adapter.lookup_count == 0
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["margin"] != 9999.99
    assert _rows_for_key(snapshot, "actionWritebacks", idempotency_key)[0]["status"] == "outcome_unknown"
    assert _event_count(snapshot["auditEvents"], "action.writeback.recovery_tick") == 1


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_operator_approval_releases_sensitive_writeback_recovery(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _margin_action_ontology(tmp_path))
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.AMBIGUOUS)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "writeback-sensitive-recovery-approved"
    margin = 7777.77

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "AdjustMargin",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"margin": margin},
            idempotency_key=idempotency_key,
            external_writeback_uri="memory://erp/orders/O-1001/margin",
            ctx=ctx,
        )

    skipped = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)
    writeback_id = cast(str, skipped["items"][0]["writebackId"])
    approved = foundry.operations.approve_action_writeback_recovery(
        writeback_id,
        approval_id="approval-writeback-1",
        reason="finance operator reviewed remote margin write",
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    approval_audit = next(
        event for event in snapshot["auditEvents"] if event["event_type"] == "action.writeback.recovery_approved"
    )

    assert skipped["items"][0].get("approvalRequired") is True
    assert approved["decision"] == "reconciled"
    assert approved["approvalId"] == "approval-writeback-1"
    assert approved["approvalReason"] == "sensitive_action_parameter"
    assert approved["remoteResourceId"] == "remote-sensitive-writeback"
    assert adapter.lookup_count == 1
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["margin"] == margin
    assert _rows_for_key(snapshot, "actionWritebacks", idempotency_key)[0]["status"] == "reconciled"
    assert approval_audit["after_ref"]["approvalId"] == "approval-writeback-1"
    assert str(margin) not in repr(approval_audit)


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
        simulate_writeback_compensation_required=True,
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

    foundry.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    action_log = foundry.materialization.run("action_log", ctx=ctx)
    rows = foundry.datasets.preview("ops.action_log", ctx=ctx, version=action_log.version_id)
    rendered_rows = repr(rows)
    assert "9999.99" not in rendered_rows
    assert "***MASKED***" in str(rows[0]["parameters_json"])
    assert "***MASKED***" in str(rows[0]["edit_patch_json"])


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
    # Can manage object sets (object:set:manage) but cannot read finance-classified
    # margin, so a margin filter must still be rejected as a masked property.
    set_manager = RequestContext(actor_user_id="eng-1", roles=("data_engineer",))
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
            ctx=set_manager,
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


def test_yaml_allowed_roles_grant_execution_beyond_the_static_permission_map(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    # ExpediteOrder exists only in ontology YAML (never in the policy's static
    # map): its allowedRoles must grant ops_manager, deny other roles, and keep
    # admin implicitly allowed per the static-map convention.
    admin_ctx = _prepare_demo_with_ontology(foundry, _expedite_action_ontology(tmp_path, "ops_manager"))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    data_engineer = RequestContext(actor_user_id="engineer-1", roles=("data_engineer",))
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    order = foundry.objects.get("Order", "O-1001", ctx=ops_manager)

    for denied_ctx in (data_engineer, viewer):
        with pytest.raises(PermissionDenied):
            foundry.actions.apply(
                "ExpediteOrder",
                object_type="Order",
                object_id="O-1001",
                expected_object_version=order["objectVersion"],
                params={"reason": "hot order"},
                idempotency_key=f"expedite-denied-{denied_ctx.actor_user_id}",
                ctx=denied_ctx,
            )

    expedited = foundry.actions.apply(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "hot order"},
        idempotency_key="expedite-ops",
        ctx=ops_manager,
    )
    admin_order = foundry.objects.get("Order", "O-1001", ctx=admin_ctx)
    admin_expedited = foundry.actions.apply(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=admin_order["objectVersion"],
        params={"reason": "admin override"},
        idempotency_key="expedite-admin",
        ctx=admin_ctx,
    )

    assert expedited["status"] == "succeeded"
    assert admin_expedited["status"] == "succeeded"
    denials = [
        event
        for event in foundry.operations.list_runs(ctx=admin_ctx)["auditEvents"]
        if event["event_type"] == "permission.denied" and event["resource_id"] == "ExpediteOrder"
    ]
    assert len(denials) == 2
    assert all(event["decision"] == "deny" for event in denials)


def test_unknown_action_is_denied_fail_closed_before_any_side_effect(foundry: FoundryLite) -> None:
    admin_ctx = prepare_indexed_demo(foundry)
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))

    with pytest.raises(PermissionDenied) as excinfo:
        foundry.actions.apply(
            "GhostAction",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=1,
            params={},
            idempotency_key="ghost-action",
            ctx=ops_manager,
        )

    runs = foundry.operations.list_runs(ctx=admin_ctx)
    assert excinfo.value.details["permission"] == "action:execute:GhostAction"
    assert _action_row_count(runs, "ghost-action") == 0
    assert any(
        event["event_type"] == "permission.denied" and event["resource_id"] == "GhostAction"
        for event in runs["auditEvents"]
    )


def test_action_execute_permission_does_not_bypass_object_read(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    # connector_ingest is granted execute through YAML allowedRoles but has no
    # object:read: it must not be able to apply or validate against a target it
    # cannot view (Palantir semantics), and each denial leaves audit evidence.
    admin_ctx = _prepare_demo_with_ontology(foundry, _expedite_action_ontology(tmp_path, "connector_ingest"))
    ingest_only = RequestContext(actor_user_id="ingest-1", roles=("connector_ingest",))
    order = foundry.objects.get("Order", "O-1001", ctx=admin_ctx)

    with pytest.raises(PermissionDenied) as apply_denied:
        foundry.actions.apply(
            "ExpediteOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "cannot read target"},
            idempotency_key="expedite-no-read",
            ctx=ingest_only,
        )
    with pytest.raises(PermissionDenied) as validate_denied:
        foundry.actions.validate(
            "ExpediteOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "cannot read target"},
            ctx=ingest_only,
        )

    # Read access alone is insufficient: Action v3 intersects execute, read,
    # and per-edit permissions before commit.
    ingest_reader = RequestContext(actor_user_id="ingest-2", roles=("connector_ingest", "viewer"))
    with pytest.raises(PermissionDenied) as edit_denied:
        foundry.actions.apply(
            "ExpediteOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "reader cannot edit"},
            idempotency_key="expedite-with-read-only",
            ctx=ingest_reader,
        )

    # Adding an object-edit role satisfies the full permission intersection.
    ingest_editor = RequestContext(actor_user_id="ingest-3", roles=("connector_ingest", "data_engineer"))
    applied = foundry.actions.apply(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "authorized editor may act"},
        idempotency_key="expedite-with-edit",
        ctx=ingest_editor,
    )

    runs = foundry.operations.list_runs(ctx=admin_ctx)
    read_denials = [
        event
        for event in runs["auditEvents"]
        if event["event_type"] == "permission.denied" and event["resource_id"] == "Order:O-1001"
    ]
    assert apply_denied.value.details["permission"] == "object:read"
    assert validate_denied.value.details["permission"] == "object:read"
    assert edit_denied.value.details["permission"] == "object:edit"
    assert applied["status"] == "succeeded"
    assert {event["action"] for event in read_denials} == {"apply", "validate"}
    assert all(event["after_ref"]["permission"] == "object:read" for event in read_denials)
    assert all(event["after_ref"]["action_type"] == "ExpediteOrder" for event in read_denials)
    assert _action_row_count(runs, "expedite-no-read") == 0


def test_action_rejects_target_object_type_mismatch_before_side_effects(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    ctx = demo_admin_context()
    idempotency_key = "action-target-mismatch"
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

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
    replay = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key=idempotency_key,
        ctx=ctx,
    )
    assert replay["status"] == "succeeded"


def test_action_rejects_same_property_target_type_mismatch(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _risk_score_action_ontology(tmp_path))
    idempotency_key = "same-property-target-mismatch"
    foundry.objects.reindex("Customer", ctx=ctx)
    customer = foundry.objects.get("Customer", "C-100", ctx=ctx)

    with pytest.raises(ValidationFailed, match="target object type mismatch"):
        foundry.actions.apply(
            "SetOrderRiskScore",
            object_type="Customer",
            object_id="C-100",
            expected_object_version=customer["objectVersion"],
            params={"riskScore": 99.0},
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    assert _action_row_count(runs, idempotency_key) == 0
    assert _rows_for_key(runs, "objectEdits", idempotency_key) == []


def test_action_rejects_corrupt_target_record_type_before_action_run(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "action-target-record-corrupt"
    with foundry.engine.begin() as conn:
        cast(Any, conn).execute(
            update(db.object_records)
            .where(
                db.object_records.c.tenant_id == ctx.tenant_id,
                db.object_records.c.object_type_api_name == "Order",
                db.object_records.c.object_id == "O-1001",
                db.object_records.c.is_active == True,  # noqa: E712
            )
            .values(object_type_id="otype_CorruptCustomer")
        )

    with pytest.raises(InvariantViolation, match="target record object type invariant"):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    assert _action_row_count(runs, idempotency_key) == 0
    assert _rows_for_key(runs, "actionWritebacks", idempotency_key) == []
    assert _rows_for_key(runs, "objectEdits", idempotency_key) == []
    assert _rows_for_key(runs, "outboxEvents", idempotency_key) == []


def test_action_accepts_record_indexed_by_compatible_prior_ontology_type(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    ontology_text = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    versioned_ontology = tmp_path / "order-customer-v2.yaml"
    versioned_ontology.write_text(
        ontology_text.replace("displayName: Order\n", "displayName: Order v2\n", 1),
        encoding="utf-8",
    )

    foundry.ontology.apply(str(versioned_ontology), ctx=ctx)
    result = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Compatible schema activation"},
        idempotency_key="compatible-prior-ontology-type",
        ctx=ctx,
    )

    assert result["status"] == "succeeded"


def _prepare_demo_with_ontology(foundry: FoundryLite, ontology_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.order_finance", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_order_finance", ctx=ctx)
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


def _expedite_action_ontology(tmp_path: Path, allowed_role: str) -> Path:
    """Demo ontology plus an ExpediteOrder action whose roles exist only in YAML.

    ExpediteOrder is intentionally absent from the policy's static permission map,
    so any grant it carries must come from ``permissions.allowedRoles`` enforcement.
    """
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    ontology = f"""{ontology}
  - apiName: ExpediteOrder
    displayName: Expedite order
    target: Order
    parameters:
      - apiName: reason
        type: string
        required: true
    permissions:
      allowedRoles: [{allowed_role}]
    mutations:
      - type: setProperty
        property: operatorNote
        valueFrom: params.reason
"""
    path = tmp_path / "order-customer-expedite-action.yaml"
    path.write_text(ontology, encoding="utf-8")
    return path


def _risk_score_action_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    ontology = ontology.replace(
        "      - apiName: riskScore\n        column: risk_score\n        type: float\n        indexed: true",
        "      - apiName: riskScore\n"
        "        column: risk_score\n"
        "        type: float\n"
        "        indexed: true\n"
        "        editable: true\n"
        "        editPolicy: edit_wins",
        1,
    )
    ontology = f"""{ontology}
  - apiName: SetOrderRiskScore
    displayName: Set order risk score
    target: Order
    parameters:
      - apiName: riskScore
        type: float
        required: true
    mutations:
      - type: setProperty
        property: riskScore
        valueFrom: params.riskScore
"""
    path = tmp_path / "order-customer-risk-score-action.yaml"
    path.write_text(ontology, encoding="utf-8")
    return path


def _status_remapped_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    path = tmp_path / "order-customer-status-remapped.yaml"
    remapped = ontology.replace(
        "        column: source_status",
        "        column: customer_id",
        1,
    )
    path.write_text(remapped, encoding="utf-8")
    return path


def _status_code_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    path = tmp_path / "order-customer-status-code.yaml"
    remapped = ontology.replace(
        "        column: source_status",
        "        column: status_code",
        1,
    )
    path.write_text(remapped, encoding="utf-8")
    return path


def _clean_orders_with_status_code(tmp_path: Path) -> Path:
    path = tmp_path / "clean_orders_status_code.csv"
    path.write_text(
        "order_id,customer_id,source_status,status_code,amount,margin,order_ts,region,risk_score\n"
        "O-1001,C-100,PENDING,BACKORDERED,1200.0,230.0,2026-06-09T10:00:00Z,NA,0.8\n"
        "O-1002,C-101,REVIEW,REVIEW,800.0,80.0,2026-06-09T11:00:00Z,EU,0.3\n"
        "O-1003,C-100,APPROVED,APPROVED,300.0,20.0,2026-06-09T12:00:00Z,NA,0.3\n",
        encoding="utf-8",
    )
    return path


def _cancel_approve_action_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    path = tmp_path / "order-customer-cancel-action.yaml"
    changed = ontology.replace("        value: APPROVED", "        value: CANCELLED", 1)
    path.write_text(changed, encoding="utf-8")
    return path
