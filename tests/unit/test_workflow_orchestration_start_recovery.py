from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureContract
from foundry_lite.application.ports.workflow_adapter import WorkflowRun, WorkflowStartRequest
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


class _RetryableRaisingWorkflowAdapter:
    profile_name = "retryable-raising"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        raise AdapterError(
            AdapterFailure(
                self.profile_name,
                "start_workflow",
                "timeout",
                True,
                "workflow start timed out",
            )
        )

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        return None


class _PermanentRaisingWorkflowAdapter(_RetryableRaisingWorkflowAdapter):
    profile_name = "permanent-raising"

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        raise RuntimeError("Authorization: Bearer raw-workflow-token")


def test_retryable_workflow_start_exception_records_start_unknown(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path, _RetryableRaisingWorkflowAdapter())

    run = foundry.operations.start_media_processing_workflow(
        media_item_version_id="miv-start-unknown",
        processor_spec={"processor": "ocr_v1"},
        idempotency_key="wf-start-timeout",
        ctx=demo_admin_context(),
    )
    replay = foundry.operations.start_media_processing_workflow(
        media_item_version_id="miv-start-unknown",
        processor_spec={"processor": "ocr_v1"},
        idempotency_key="wf-start-timeout",
        ctx=demo_admin_context(),
    )

    assert run["status"] == "start_unknown"
    assert run["error"] is not None and run["error"]["kind"] == "timeout"
    assert replay["status"] == "start_unknown"


def test_permanent_workflow_start_exception_records_failed_not_starting(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path, _PermanentRaisingWorkflowAdapter())

    run = foundry.operations.start_media_processing_workflow(
        media_item_version_id="miv-failed",
        processor_spec={"processor": "ocr_v1"},
        idempotency_key="wf-start-runtime-error",
        ctx=demo_admin_context(),
    )

    assert run["status"] == "failed"
    assert run["error"] is not None
    assert run["error"]["kind"] == "unknown"
    assert run["error"]["details"] == {"errorType": "RuntimeError"}


def _foundry(tmp_path: Path, adapter: object) -> FoundryLite:
    deps = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLite(dependencies=replace(deps, workflow_adapter=adapter))
