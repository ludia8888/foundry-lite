from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ConnectorSnapshot, ConnectorSnapshotRequest, RuntimeRowsTable
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.workflow_adapter import (
    WorkflowAdapter,
    WorkflowRun,
    WorkflowStartRequest,
    workflow_run_id,
)
from foundry_lite.application.services import workflow_orchestration_helpers as workflow_helpers
from foundry_lite.application.services.workflow_connector_sync import connector_sync_workflow_output
from foundry_lite.application.services.workflow_orchestration_service import CONNECTOR_SYNC_WORKFLOW_NAME
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure.adapters import FakeWorkflowAdapter, LocalConnectorAdapter, LocalWorkflowAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.fixture(params=[LocalWorkflowAdapter, FakeWorkflowAdapter])
def adapter(request: pytest.FixtureRequest) -> WorkflowAdapter:
    adapter_type = request.param
    return adapter_type()


class RetryableStartFailureWorkflowAdapter:
    profile_name = "retryable-start-failure"

    def __init__(self) -> None:
        self.start_calls = 0

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.start_calls += 1
        return WorkflowRun(
            run_id=workflow_run_id(request),
            workflow_name=request.workflow_name,
            status="failed",
            output={},
            error={"kind": "unavailable", "retryable": True},
        )

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        return None

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        return None


class StartUnknownThenSuccessWorkflowAdapter:
    profile_name = "start-unknown-then-success"

    def __init__(self) -> None:
        self.inputs: list[Mapping[str, object]] = []

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.inputs.append(dict(request.input))
        if len(self.inputs) == 1:
            return WorkflowRun(
                run_id=workflow_run_id(request),
                workflow_name=request.workflow_name,
                status="failed",
                output={},
                error={"kind": "timeout", "retryable": True},
            )
        return WorkflowRun(
            run_id=workflow_run_id(request),
            workflow_name=request.workflow_name,
            status="succeeded",
            output={"processed": True, **dict(request.input)},
        )

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        return None

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        return None


class ResponseLossAfterActivityWorkflowAdapter:
    profile_name = "response-loss-after-activity"

    def __init__(self, driver: Callable[[Mapping[str, object]], Mapping[str, object]]) -> None:
        self.start_calls = 0
        self.lookup_calls = 0
        self._driver = driver
        self._run_tenant_id: str | None = None
        self._completed_run: WorkflowRun | None = None

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.start_calls += 1
        if self._completed_run is None:
            output = self._driver(request.input)
            self._completed_run = WorkflowRun(
                run_id=workflow_run_id(request),
                workflow_name=request.workflow_name,
                status="succeeded",
                output=output,
            )
            self._run_tenant_id = request.tenant_id
        return WorkflowRun(
            run_id=workflow_run_id(request),
            workflow_name=request.workflow_name,
            status="failed",
            output={},
            error={"kind": "timeout", "retryable": True},
        )

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        self.lookup_calls += 1
        if tenant_id != self._run_tenant_id:
            return None
        if self._completed_run is None or self._completed_run.run_id != run_id:
            return None
        return self._completed_run

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        return None


class DriverWorkflowAdapter:
    profile_name = "driver-workflow"

    def __init__(self, driver: Callable[[Mapping[str, object]], Mapping[str, object]]) -> None:
        self.start_calls = 0
        self._driver = driver
        self._runs: dict[tuple[str, str], WorkflowRun] = {}

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.start_calls += 1
        key = (request.tenant_id, workflow_run_id(request))
        if key not in self._runs:
            self._runs[key] = WorkflowRun(
                run_id=key[1],
                workflow_name=request.workflow_name,
                status="succeeded",
                output=self._driver(request.input),
            )
        return self._runs[key]

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        return self._runs.get((tenant_id, run_id))

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        return None


class PostCommitCrash(RuntimeError):
    """Simulates an activity worker dying after the dataset commit succeeded."""


class ActivityRetryWorkflowAdapter:
    profile_name = "activity-retry"

    def __init__(self, driver: Callable[[Mapping[str, object]], Mapping[str, object]]) -> None:
        self.start_calls = 0
        self.driver_calls = 0
        self._driver = driver
        self._run: WorkflowRun | None = None
        self._run_tenant_id: str | None = None

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.start_calls += 1
        if self._run is None:
            self._run = self._run_with_one_activity_retry(request)
            self._run_tenant_id = request.tenant_id
        return self._run

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        if tenant_id != self._run_tenant_id or self._run is None:
            return None
        return self._run if self._run.run_id == run_id else None

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        return None

    def _run_with_one_activity_retry(self, request: WorkflowStartRequest) -> WorkflowRun:
        for _attempt in range(2):
            self.driver_calls += 1
            try:
                output = self._driver(request.input)
            except PostCommitCrash:
                continue
            return WorkflowRun(
                run_id=workflow_run_id(request),
                workflow_name=request.workflow_name,
                status="succeeded",
                output=output,
            )
        raise AssertionError("activity retry did not recover committed sync run")


class CancellableRunningWorkflowAdapter:
    profile_name = "cancellable-running"

    def __init__(self) -> None:
        self.cancel_calls = 0
        self._run_tenant_id: str | None = None
        self._run: WorkflowRun | None = None

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        run = WorkflowRun(
            run_id=workflow_run_id(request),
            workflow_name=request.workflow_name,
            status="running",
            output=dict(request.input),
        )
        self._run = run
        self._run_tenant_id = request.tenant_id
        return run

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        if tenant_id != self._run_tenant_id:
            return None
        if self._run is None or self._run.run_id != run_id:
            return None
        return self._run

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        self.cancel_calls += 1
        run = self.workflow_run(tenant_id, run_id)
        if run is None:
            return None
        cancelled = WorkflowRun(
            run_id=run.run_id,
            workflow_name=run.workflow_name,
            status="cancelled",
            output={
                **dict(run.output),
                "cancellation": {
                    "reason": reason,
                    "cleanup": {"datasetStaging": "adapter_confirmed", "committedVersionId": None},
                },
            },
        )
        self._run = cancelled
        return cancelled


class MismatchedCancelWorkflowAdapter(CancellableRunningWorkflowAdapter):
    profile_name = "mismatched-cancel"

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        run = super().cancel_workflow(tenant_id, run_id, reason=reason)
        if run is None:
            return None
        return WorkflowRun(
            run_id=f"{run.run_id}:other",
            workflow_name=run.workflow_name,
            status=run.status,
            output=dict(run.output),
            error=run.error,
        )


class TerminalAfterCancelWorkflowAdapter(CancellableRunningWorkflowAdapter):
    profile_name = "terminal-after-cancel"

    def cancel_workflow(self, tenant_id: str, run_id: str, *, reason: str | None = None) -> WorkflowRun | None:
        run = self.workflow_run(tenant_id, run_id)
        if run is None:
            return None
        return WorkflowRun(
            run_id=run.run_id,
            workflow_name=run.workflow_name,
            status="succeeded",
            output={**dict(run.output), "lateResult": "already_committed"},
        )


def test_workflow_adapter_contract_starts_and_reads_tenant_scoped_run(adapter: WorkflowAdapter) -> None:
    request = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-demo",
        request_id="req-workflow-1",
        idempotency_key="sync-orders-1",
        input={"dataset": "raw.orders"},
    )

    run = adapter.start_workflow(request)
    fetched = adapter.workflow_run(request.tenant_id, run.run_id)
    cross_tenant = adapter.workflow_run("tenant-other", run.run_id)

    assert run.status == "succeeded"
    assert run.workflow_name == "sync_orders"
    assert run.output["request_id"] == "req-workflow-1"
    assert fetched == run
    assert cross_tenant is None


def test_workflow_adapter_contract_is_idempotent(adapter: WorkflowAdapter) -> None:
    request = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-demo",
        request_id="req-workflow-2",
        idempotency_key="sync-orders-repeat",
        input={"dataset": "raw.orders"},
    )

    first = adapter.start_workflow(request)
    second = adapter.start_workflow(request)

    assert second == first
    assert adapter.workflow_run(request.tenant_id, "missing") is None


def test_workflow_adapter_contract_cancel_is_tenant_scoped(adapter: WorkflowAdapter) -> None:
    request = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-demo",
        request_id="req-workflow-cancel",
        idempotency_key="sync-orders-cancel",
        input={"dataset": "raw.orders"},
    )

    run = adapter.start_workflow(request)

    assert adapter.cancel_workflow("tenant-other", run.run_id, reason="stop") is None
    assert adapter.cancel_workflow(request.tenant_id, "missing", reason="stop") is None
    assert adapter.cancel_workflow(request.tenant_id, run.run_id, reason="stop") == run


def test_workflow_adapter_contract_namespaces_idempotency_by_tenant_and_workflow(adapter: WorkflowAdapter) -> None:
    base = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-a",
        request_id="req-workflow-a",
        idempotency_key="daily-sync",
        input={"dataset": "raw.orders"},
    )
    other_tenant = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-b",
        request_id="req-workflow-b",
        idempotency_key="daily-sync",
        input={"dataset": "raw.orders"},
    )
    other_workflow = WorkflowStartRequest(
        workflow_name="sync_customers",
        tenant_id="tenant-a",
        request_id="req-workflow-c",
        idempotency_key="daily-sync",
        input={"dataset": "raw.customers"},
    )

    runs = [adapter.start_workflow(request) for request in (base, other_tenant, other_workflow)]

    assert len({run.run_id for run in runs}) == 3
    assert all(run.run_id.startswith("flite:") for run in runs)
    assert all("daily-sync" not in run.run_id for run in runs)
    assert adapter.workflow_run(base.tenant_id, runs[0].run_id) == runs[0]
    assert adapter.workflow_run(other_tenant.tenant_id, runs[0].run_id) is None


def test_product_workflow_operations_contract_starts_connector_sync_and_audits(
    adapter: WorkflowAdapter,
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-contract")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    run = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="connector-sync-orders",
        ctx=ctx,
    )
    duplicate = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="connector-sync-orders",
        ctx=ctx,
    )

    assert str(run["workflowRunId"]).startswith("flite:")
    assert run["workflowRunId"] != "connector-sync-orders"
    assert duplicate["workflowRunId"] == run["workflowRunId"]
    assert run["idempotencyKey"] == "connector-sync-orders"
    assert duplicate["idempotencyKey"] == "connector-sync-orders"
    assert run["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
    assert run["workflowProfile"] == adapter.profile_name
    assert run["status"] == "succeeded"
    assert run["output"]["datasetRef"] == "raw.workflow_orders"
    assert run["operationPath"] == f"/api/operations/runs/workflow/{run['workflowRunId']}"
    assert run["foundryRunId"] is not None
    assert duplicate["foundryRunId"] == run["foundryRunId"]
    workflow_runs = foundry.operations.query_runs(ctx=ctx, run_type="workflow")["workflowRuns"]
    detail = foundry.operations.run_detail("audit", str(run["foundryRunId"]), ctx=ctx)
    workflow_detail = foundry.operations.run_detail("workflow", str(run["workflowRunId"]), ctx=ctx)
    audits = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]
    workflow_audits = [event for event in audits if event["resource_id"] == run["workflowRunId"]]
    assert [row["id"] for row in workflow_runs] == [run["workflowRunId"]]
    assert workflow_runs[0]["status"] == "succeeded"
    assert detail["row"]["resource_id"] == run["workflowRunId"]
    after_ref = detail["row"]["after_ref"]
    assert isinstance(after_ref, Mapping)
    assert after_ref["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
    assert any(
        relation["target_run_id"] == run["foundryRunId"] and relation["relation"] == "audited_by"
        for relation in workflow_detail["runRelations"]
    )
    assert len(workflow_audits) == 1


def test_product_workflow_operations_contract_same_key_different_request_conflicts(
    adapter: WorkflowAdapter,
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-conflict-contract")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.workflow_customers", ctx=ctx, primary_key=["customer_id"])

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="shared-key",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected):
        foundry.operations.start_connector_sync_workflow(
            "raw.workflow_customers",
            connector_name="rest",
            resource_name="customers",
            idempotency_key="shared-key",
            ctx=ctx,
        )

    workflow_runs = foundry.operations.query_runs(ctx=ctx, run_type="workflow")["workflowRuns"]
    assert [row["id"] for row in workflow_runs] == [first["workflowRunId"]]


def test_product_workflow_operations_contract_retryable_start_failure_stays_retryable(
    tmp_path: Path,
) -> None:
    adapter = RetryableStartFailureWorkflowAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-start-unknown-contract")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="transient-key",
        ctx=ctx,
    )
    second = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="transient-key",
        ctx=ctx,
    )

    workflow_runs = foundry.operations.query_runs(ctx=ctx, run_type="workflow")["workflowRuns"]
    assert first["workflowRunId"] == second["workflowRunId"]
    assert first["status"] == "start_unknown"
    assert second["status"] == "start_unknown"
    assert second["foundryRunId"] == first["foundryRunId"]
    assert adapter.start_calls == 2
    assert workflow_runs[0]["status"] == "start_unknown"
    assert workflow_runs[0]["attempts"] == 2
    assert workflow_runs[0]["error"] == {"kind": "unavailable", "retryable": True}


def test_product_workflow_start_unknown_retry_preserves_persisted_definition_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = StartUnknownThenSuccessWorkflowAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-definition-version")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])
    monkeypatch.setattr(
        workflow_helpers,
        "CONNECTOR_SYNC_WORKFLOW_DEFINITION_VERSION",
        "connector_sync.workflow/v1",
    )

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="definition-version-key",
        ctx=ctx,
    )
    monkeypatch.setattr(
        workflow_helpers,
        "CONNECTOR_SYNC_WORKFLOW_DEFINITION_VERSION",
        "connector_sync.workflow/v2",
    )
    replay = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="definition-version-key",
        ctx=ctx,
    )

    assert first["status"] == "start_unknown"
    assert replay["status"] == "succeeded"
    assert replay["output"]["workflowDefinitionVersion"] == "connector_sync.workflow/v1"
    assert [item["workflowDefinitionVersion"] for item in adapter.inputs] == [
        "connector_sync.workflow/v1",
        "connector_sync.workflow/v1",
    ]


def test_product_workflow_reconciles_activity_completion_response_loss(tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry: FoundryLite | None = None

    def driver(payload: Mapping[str, object]) -> Mapping[str, object]:
        assert foundry is not None
        return _connector_sync_driver_output(foundry, ctx.tenant_id, payload, ctx=ctx)

    adapter = ResponseLossAfterActivityWorkflowAdapter(driver)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-response-loss")
    foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=_connector_adapter(ctx.tenant_id),
            workflow_adapter=adapter,
        )
    )
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="local",
        resource_name="orders",
        idempotency_key="response-loss-key",
        ctx=ctx,
    )
    second = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="local",
        resource_name="orders",
        idempotency_key="response-loss-key",
        ctx=ctx,
    )

    assert first["status"] == "start_unknown"
    assert second["status"] == "succeeded"
    assert second["output"]["datasetRef"] == "raw.workflow_orders"
    assert second["output"]["rowCount"] == 1
    assert adapter.start_calls == 1
    assert adapter.lookup_calls == 1
    assert foundry.datasets.preview("raw.workflow_orders", ctx=ctx)[0]["order_id"] == "O-RESPONSE-LOSS"
    workflow_detail = foundry.operations.run_detail("workflow", str(second["workflowRunId"]), ctx=ctx)
    assert any(
        relation["relation"] == "reconciled_by"
        and relation["metadata"]["previousStatus"] == "start_unknown"
        and relation["metadata"]["status"] == "succeeded"
        for relation in workflow_detail["runRelations"]
    )
    audit_events = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]
    assert any(event["event_type"] == "workflow.reconciled" for event in audit_events)


def test_connector_sync_workflow_commits_then_advances_cursor(tmp_path: Path) -> None:
    ctx = demo_admin_context()
    connector_adapter = CursorAwareConnectorAdapter()
    foundry: FoundryLite | None = None

    def driver(payload: Mapping[str, object]) -> Mapping[str, object]:
        assert foundry is not None
        return _connector_sync_driver_output(foundry, ctx.tenant_id, payload, ctx=ctx)

    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-cursor-chain")
    foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=connector_adapter,
            workflow_adapter=DriverWorkflowAdapter(driver),
        )
    )
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="cursor",
        resource_name="orders",
        idempotency_key="cursor-page-1",
        ctx=ctx,
    )
    second = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="cursor",
        resource_name="orders",
        idempotency_key="cursor-page-2",
        ctx=ctx,
    )

    assert connector_adapter.requests[0].cursor is None
    assert connector_adapter.requests[1].cursor == {"cursor": "page-2"}
    first_data_plane = cast(Mapping[str, object], first["output"]["dataPlane"])
    first_cursor = cast(Mapping[str, object], first_data_plane["cursor"])
    second_data_plane = cast(Mapping[str, object], second["output"]["dataPlane"])
    second_cursor = cast(Mapping[str, object], second_data_plane["cursor"])
    second_stages = cast(list[Mapping[str, object]], second_data_plane["stages"])
    assert first_cursor["nextCursor"] == {"cursor": "page-2"}
    assert second_cursor["requestCursor"] == {"cursor": "page-2"}
    assert second_cursor["nextCursor"] is None
    assert [stage["name"] for stage in second_stages] == [
        "fetchPage",
        "rawStagingWrite",
        "qualityCheck",
        "datasetCommit",
        "cursorAdvance",
        "eventEmit",
    ]


def test_worker_crash_mid_activity_replays_without_duplicate_commit(tmp_path: Path) -> None:
    ctx = demo_admin_context()
    connector_adapter = CountingConnectorAdapter()
    foundry: FoundryLite | None = None
    is_first_activity_attempt = True

    def driver(payload: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal is_first_activity_attempt
        assert foundry is not None
        output = _connector_sync_driver_output(foundry, ctx.tenant_id, payload, ctx=ctx)
        if is_first_activity_attempt:
            is_first_activity_attempt = False
            raise PostCommitCrash("worker died after dataset commit")
        return output

    workflow_adapter = ActivityRetryWorkflowAdapter(driver)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-activity-retry")
    foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=connector_adapter,
            workflow_adapter=workflow_adapter,
        )
    )
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    run = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="counting",
        resource_name="orders",
        idempotency_key="activity-crash-after-commit",
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    data_plane = cast(Mapping[str, object], run["output"]["dataPlane"])
    assert data_plane["outboxEventType"] == "dataset.version.committed"
    assert connector_adapter.snapshot_calls == 1
    assert workflow_adapter.driver_calls == 2
    assert len(foundry.datasets.list_versions("raw.workflow_orders", ctx=ctx)) == 1
    assert _tenant_row_count(foundry, ctx.tenant_id, "sync_runs") == 1
    assert _outbox_event_count(foundry, ctx.tenant_id, "dataset.version.committed") == 1


def test_product_workflow_cancel_cleans_running_workflow_and_audits(tmp_path: Path) -> None:
    adapter = CancellableRunningWorkflowAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-cancel")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    started = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="cancel-key",
        ctx=ctx,
    )
    cancelled = foundry.operations.cancel_product_workflow(
        str(started["workflowRunId"]),
        reason="operator requested stop",
        ctx=ctx,
    )
    duplicate = foundry.operations.cancel_product_workflow(
        str(started["workflowRunId"]),
        reason="operator requested stop",
        ctx=ctx,
    )

    assert started["status"] == "running"
    assert cancelled["status"] == "cancelled"
    assert duplicate["status"] == "cancelled"
    assert adapter.cancel_calls == 1
    output = cancelled["output"]
    assert isinstance(output, dict)
    cancellation = output["cancellation"]
    assert isinstance(cancellation, dict)
    cleanup = cancellation["cleanup"]
    assert isinstance(cleanup, dict)
    assert cancellation["reason"] == "operator requested stop"
    assert cleanup == {
        "datasetStaging": "adapter_confirmed",
        "committedVersionId": None,
    }
    workflow_detail = foundry.operations.run_detail("workflow", str(cancelled["workflowRunId"]), ctx=ctx)
    assert any(
        relation["relation"] == "cancelled_by"
        and relation["metadata"]["previousStatus"] == "running"
        and relation["metadata"]["status"] == "cancelled"
        for relation in workflow_detail["runRelations"]
    )
    audit_events = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]
    cancel_audits = [event for event in audit_events if event["event_type"] == "workflow.cancelled"]
    assert len(cancel_audits) == 1
    after_ref = cancel_audits[0]["after_ref"]
    assert isinstance(after_ref, dict)
    after_cancellation = after_ref["cancellation"]
    assert isinstance(after_cancellation, dict)
    after_cleanup = after_cancellation["cleanup"]
    assert isinstance(after_cleanup, dict)
    assert after_cleanup["datasetStaging"] == "adapter_confirmed"


def test_product_workflow_cancel_rejects_mismatched_adapter_result(tmp_path: Path) -> None:
    ctx = demo_admin_context()
    adapter = MismatchedCancelWorkflowAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-cancel-mismatch")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    started = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="cancel-mismatch-key",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected, match="different workflow run"):
        foundry.operations.cancel_product_workflow(
            str(started["workflowRunId"]),
            reason="operator requested stop",
            ctx=ctx,
        )


def test_product_workflow_cancel_rejects_terminal_non_cancelled_adapter_result(tmp_path: Path) -> None:
    ctx = demo_admin_context()
    adapter = TerminalAfterCancelWorkflowAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-cancel-terminal")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    started = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="cancel-terminal-key",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected, match="terminal non-cancelled"):
        foundry.operations.cancel_product_workflow(
            str(started["workflowRunId"]),
            reason="operator requested stop",
            ctx=ctx,
        )
    row = foundry.operations.product_workflow_run(str(started["workflowRunId"]), ctx=ctx)
    assert row["status"] == "running"
    audit_events = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]
    assert not any(event["event_type"] == "workflow.cancelled" for event in audit_events)


def _connector_adapter(tenant_id: str) -> LocalConnectorAdapter:
    return LocalConnectorAdapter(
        {
            (tenant_id, "local", "orders"): ConnectorSnapshot(
                connector_name="local",
                resource_name="orders",
                rows=({"order_id": "O-RESPONSE-LOSS", "amount": 42},),
                schema={"columns": ["order_id", "amount"]},
                cursor={"cursor": "done"},
                source_watermark="workflow-response-loss",
            )
        }
    )


class CursorAwareConnectorAdapter:
    profile_name = "cursor-aware"

    def __init__(self) -> None:
        self.requests: list[ConnectorSnapshotRequest] = []

    def failure_contract(self) -> AdapterFailureContract:
        return LocalConnectorAdapter({}).failure_contract()

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        self.requests.append(request)
        if request.cursor == {"cursor": "page-2"}:
            return ConnectorSnapshot(
                connector_name=request.connector_name,
                resource_name=request.resource_name,
                rows=({"order_id": "O-CURSOR-2", "amount": 22},),
                schema={"columns": ["order_id", "amount"]},
                cursor=None,
                source_watermark="cursor-page-2",
            )
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=({"order_id": "O-CURSOR-1", "amount": 11},),
            schema={"columns": ["order_id", "amount"]},
            cursor={"cursor": "page-2"},
            source_watermark="cursor-page-1",
        )


class CountingConnectorAdapter:
    profile_name = "counting"

    def __init__(self) -> None:
        self.snapshot_calls = 0

    def failure_contract(self) -> AdapterFailureContract:
        return LocalConnectorAdapter({}).failure_contract()

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        self.snapshot_calls += 1
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=({"order_id": "O-ACTIVITY-RETRY", "amount": 77},),
            schema={"columns": ["order_id", "amount"]},
            cursor={"cursor": "done"},
            source_watermark="activity-retry",
        )


def _connector_sync_driver_output(
    foundry: FoundryLite,
    tenant_id: str,
    payload: Mapping[str, object],
    *,
    ctx: RequestContext,
) -> Mapping[str, object]:
    result = foundry.datasets.sync_connector_snapshot(
        str(payload["datasetRef"]),
        connector_name=str(payload["connectorName"]),
        resource_name=str(payload["resourceName"]),
        sync_name=_payload_optional_text(payload, "syncName"),
        ctx=ctx,
        run_id=_payload_text(payload, "syncRunId"),
    )
    metadata = _committed_transaction_metadata(foundry, tenant_id, result.version_id)
    return connector_sync_workflow_output(result, payload=payload, transaction_metadata=metadata)


def _committed_transaction_metadata(
    foundry: FoundryLite,
    tenant_id: str,
    version_id: str,
) -> Mapping[str, object]:
    with foundry.engine.begin() as conn:
        tx = foundry.dataset_transaction_repository.committed_transaction_by_version(
            transaction=conn,
            tenant_id=tenant_id,
            committed_version_id=version_id,
        )
    assert tx is not None
    return tx["metadata"]


def _tenant_row_count(foundry: FoundryLite, tenant_id: str, table: RuntimeRowsTable) -> int:
    with foundry.engine.begin() as conn:
        rows = foundry.runtime_repository.rows_for_tenant(transaction=conn, table=table, tenant_id=tenant_id)
    return len(rows)


def _outbox_event_count(foundry: FoundryLite, tenant_id: str, event_type: str) -> int:
    with foundry.engine.begin() as conn:
        rows = foundry.runtime_repository.rows_for_tenant(transaction=conn, table="outbox_events", tenant_id=tenant_id)
    return len([row for row in rows if row["event_type"] == event_type])


def _payload_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    assert isinstance(value, str)
    return value


def _payload_optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
