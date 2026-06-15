from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
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
    core: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(core)

    order = core.objects.get("Order", "O-1001", ctx=ctx, include_explain=True)
    customer = core.objects.get("Customer", "C-100", ctx=ctx)
    linked_customer = core.objects.links("Order", "O-1001", "OrderCustomer")[0]["to"]

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
    core: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(core)
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
        core.ontology.apply(bad_yaml, ctx=ctx)


@pytest.mark.integration_scenario("object_action_audit")
def test_action_apply_is_idempotent_and_rejects_stale_object_version(
    core: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    first = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=ctx,
    )
    replay = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=ctx,
    )

    approved = core.objects.get("Order", "O-1001", ctx=ctx)
    runs = core.operations.list_runs(ctx=ctx)
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
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Different body with reused key"},
            idempotency_key="same-key",
            ctx=ctx,
        )
    conflict_runs = core.operations.list_runs(ctx=ctx)
    assert idempotency_conflict.value.details["action_run_id"] == first["actionRunId"]
    assert any(
        event["event_type"] == "action.run.idempotency_conflict" and event["resource_id"] == first["actionRunId"]
        for event in conflict_runs["auditEvents"]
    )
    with pytest.raises(ConflictDetected):
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed again"},
            idempotency_key="different-key",
            ctx=ctx,
        )


def test_action_same_idempotency_key_different_body_returns_409(core: FoundryLite) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    first = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key-different-body",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected) as exc_info:
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Different body"},
            idempotency_key="same-key-different-body",
            ctx=ctx,
        )

    runs = core.operations.list_runs(ctx=ctx)
    assert exc_info.value.details["action_run_id"] == first["actionRunId"]
    assert any(
        event["event_type"] == "action.run.idempotency_conflict" and event["resource_id"] == first["actionRunId"]
        for event in runs["auditEvents"]
    )


def test_action_precondition_stale_read_conflicts_on_commit(core: FoundryLite) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "First writer"},
        idempotency_key="first-writer",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected):
        core.actions.apply(
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
    core: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.objects.get("Order", "O-1001", ctx=ctx)

    with pytest.raises(ExternalSystemError):
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="writeback-fails",
            simulate_writeback_failure=True,
            ctx=ctx,
        )

    after = core.objects.get("Order", "O-1001", ctx=ctx)
    failed_runs = [
        run for run in core.operations.list_runs(ctx=ctx)["actionRuns"] if run["idempotency_key"] == "writeback-fails"
    ]
    replay = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="writeback-fails",
        ctx=ctx,
    )
    after_replay_runs = [
        run for run in core.operations.list_runs(ctx=ctx)["actionRuns"] if run["idempotency_key"] == "writeback-fails"
    ]
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert len(failed_runs) == 1
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "failed"
    assert replay["actionRunId"] == failed_runs[0]["id"]
    assert [run["id"] for run in after_replay_runs] == [failed_runs[0]["id"]]
    writeback = core.operations.list_runs(ctx=ctx)["actionWritebacks"][0]
    writeback_request = cast(Mapping[str, object], writeback["request"])
    writeback_response = cast(Mapping[str, object], writeback["response"])
    assert writeback["connector_id"] == "mock_erp_simulator"
    assert writeback_request["networkCall"] is False
    assert writeback_response["simulated"] is True
    audit_events = core.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "action.run.failed" for event in audit_events)


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
    core, failing_repository = _core_with_action_failure(tmp_path, fail_point)
    ctx = prepare_indexed_demo(core)
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    before = core.operations.list_runs(ctx=ctx)

    with pytest.raises(_InjectedActionCommitFailure):
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Atomicity proof"},
            idempotency_key="atomicity-proof",
            ctx=ctx,
        )

    after_failure = core.operations.list_runs(ctx=ctx)
    unchanged = core.objects.get("Order", "O-1001", ctx=ctx)
    assert unchanged["objectVersion"] == order["objectVersion"]
    assert unchanged["properties"]["status"] == "PENDING"
    assert _action_row_count(after_failure, "atomicity-proof") == 0
    assert _action_commit_evidence_counts(after_failure) == _action_commit_evidence_counts(before)

    _disable_injected_failure(failing_repository)
    retry = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Atomicity proof"},
        idempotency_key="atomicity-proof",
        ctx=ctx,
    )
    assert retry["status"] == "succeeded"


def test_outbox_event_not_published_before_domain_commit(tmp_path: Path) -> None:
    # The action emits outbox events through the runtime outbox table inside the
    # same domain transaction; there is no direct external publish call. Injecting
    # a failure on the second outbox insert rolls the whole commit back, so the
    # earlier action.run.committed outbox row inserted in the same transaction
    # must also vanish: an outbox event is never durable (and therefore never
    # publishable by the outbox drain path) before the domain commit succeeds.
    core, failing_repository = _core_with_action_failure(tmp_path, "outbox:object.edit.committed")
    ctx = prepare_indexed_demo(core)
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    before = _action_commit_evidence_counts(core.operations.list_runs(ctx=ctx))

    with pytest.raises(_InjectedActionCommitFailure):
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Outbox-before-commit proof"},
            idempotency_key="outbox-before-commit",
            ctx=ctx,
        )

    after_failure = core.operations.list_runs(ctx=ctx)
    assert _action_commit_evidence_counts(after_failure) == before
    assert _event_count(after_failure["outboxEvents"], "action.run.committed") == before["outbox_action"]
    assert _event_count(after_failure["outboxEvents"], "object.edit.committed") == before["outbox_edit"]

    _disable_injected_failure(failing_repository)
    committed = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Outbox-before-commit proof"},
        idempotency_key="outbox-before-commit",
        ctx=ctx,
    )
    after_commit = _action_commit_evidence_counts(core.operations.list_runs(ctx=ctx))

    # Only after the domain commit succeeds do the outbox rows appear.
    assert committed["status"] == "succeeded"
    assert after_commit["outbox_action"] == before["outbox_action"] + 1
    assert after_commit["outbox_edit"] == before["outbox_edit"] + 1


def test_action_audit_masks_sensitive_params(core: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_demo_with_ontology(core, _margin_action_ontology(tmp_path))
    order = core.objects.get("Order", "O-1001", ctx=ctx)

    result = core.actions.apply(
        "AdjustMargin",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"margin": 9999.99},
        idempotency_key="audit-mask-margin",
        ctx=ctx,
    )

    changed = core.objects.get("Order", "O-1001", ctx=ctx)
    audit = next(
        event
        for event in core.operations.list_runs(ctx=ctx)["auditEvents"]
        if event["event_type"] == "action.run.committed" and event["resource_id"] == result["actionRunId"]
    )
    assert changed["properties"]["margin"] == 9999.99
    assert result["patch"]["margin"] == 9999.99
    assert audit["before_ref"]["margin"] == "***MASKED***"
    assert audit["after_ref"]["patch"]["margin"] == "***MASKED***"


@pytest.mark.integration_scenario("permission_tenant_isolation")
def test_viewer_sees_masked_margin_and_cannot_approve_order(
    core: FoundryLite,
) -> None:
    prepare_indexed_demo(core)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    finance = RequestContext(actor_user_id="finance-1", roles=("finance",))
    other_tenant = RequestContext(
        tenant_id="tenant-other",
        actor_user_id="other-admin",
        roles=("admin", "data_engineer", "ops_manager", "finance"),
    )
    order = core.objects.get("Order", "O-1001", ctx=viewer)
    dataset_preview = core.datasets.preview("clean.orders", ctx=viewer, limit=1)
    finance_order = core.objects.get("Order", "O-1001", ctx=finance)
    finance_query = core.objects.query(
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
        core.objects.query(
            "Order",
            ctx=viewer,
            filter_ast={"property": "margin", "op": "gte", "value": 80.0},
        )
    with pytest.raises(ValidationFailed, match="masked property"):
        core.objects.query(
            "Order",
            ctx=viewer,
            order_by=[{"property": "margin", "direction": "desc"}],
        )
    with pytest.raises(ValidationFailed, match="masked property"):
        core.objects.create_set(
            "Masked Margin Orders",
            "Order",
            set_type="dynamic",
            filter_ast={"property": "margin", "op": "gte", "value": 80.0},
            ctx=viewer,
        )
    with pytest.raises(PermissionDenied):
        core.ontology.apply(str(DEMO_ROOT / "ontology" / "order-customer.yaml"), ctx=viewer)
    with pytest.raises(NotFound):
        core.objects.get("Order", "O-1001", ctx=other_tenant)
    assert all(not rows for rows in core.operations.list_runs(ctx=other_tenant).values())
    with pytest.raises(PermissionDenied):
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="viewer-denied",
            ctx=viewer,
        )
    assert any(event["decision"] == "deny" for event in core.operations.list_runs()["auditEvents"])


def test_only_ops_manager_or_admin_can_execute_approve_order(
    core: FoundryLite,
) -> None:
    prepare_indexed_demo(core)
    data_engineer = RequestContext(actor_user_id="engineer-1", roles=("data_engineer",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    order = core.objects.get("Order", "O-1001", ctx=ops_manager)

    with pytest.raises(PermissionDenied):
        core.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="engineer-denied",
            ctx=data_engineer,
        )

    approved = core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="ops-approved",
        ctx=ops_manager,
    )

    assert approved["status"] == "succeeded"
    assert any(event["decision"] == "deny" for event in core.operations.list_runs()["auditEvents"])


def _prepare_demo_with_ontology(core: FoundryLite, ontology_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    core.demo.seed_files()
    core.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    core.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    core.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    core.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    core.demo.register_transforms(ctx)
    core.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    core.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    core.transforms.run("clean_orders", ctx=ctx)
    core.transforms.run("clean_customers", ctx=ctx)
    core.ontology.apply(str(ontology_path), ctx=ctx)
    core.objects.reindex("Order", ctx=ctx)
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
