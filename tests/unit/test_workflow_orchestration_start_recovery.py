from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureContract
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter, WorkflowRun, WorkflowStartRequest
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


class _RetryableRaisingWorkflowAdapter:
    profile_name = "retryable-raising"

    def __init__(self) -> None:
        self.starts = 0

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.starts += 1
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
        self.starts += 1
        raise RuntimeError("Authorization: Bearer raw-workflow-token")


class _SensitiveAdapterFailureWorkflowAdapter(_RetryableRaisingWorkflowAdapter):
    profile_name = "sensitive-adapter-failure"

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.starts += 1
        raise AdapterError(
            AdapterFailure(
                self.profile_name,
                "start_workflow",
                "timeout",
                True,
                "Authorization: Bearer raw-workflow-token",
                details={"apiKey": "plain-key", "safe": "visible", "nested": {"providerResponse": "raw"}},
            )
        )


def test_retryable_workflow_start_exception_records_start_unknown(tmp_path: Path) -> None:
    adapter = _RetryableRaisingWorkflowAdapter()
    foundry = _foundry(tmp_path, adapter)

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
    assert adapter.starts == 2


def test_workflow_start_exception_scrubs_adapter_failure_evidence(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path, _SensitiveAdapterFailureWorkflowAdapter())

    run = foundry.operations.start_media_processing_workflow(
        media_item_version_id="miv-sensitive-start-failure",
        processor_spec={"processor": "ocr_v1"},
        idempotency_key="wf-sensitive-start-failure",
        ctx=demo_admin_context(),
    )

    assert run["status"] == "start_unknown"
    assert run["error"] is not None
    assert run["error"]["operatorMessage"] == "***MASKED***"
    assert run["error"]["details"] == {
        "apiKey": "***MASKED***",
        "safe": "visible",
        "nested": {"providerResponse": "***MASKED***"},
    }
    assert "raw-workflow-token" not in str(run["error"])
    assert "plain-key" not in str(run["error"])


def test_permanent_workflow_start_exception_records_failed_not_starting(tmp_path: Path) -> None:
    adapter = _PermanentRaisingWorkflowAdapter()
    foundry = _foundry(tmp_path, adapter)

    run = foundry.operations.start_media_processing_workflow(
        media_item_version_id="miv-failed",
        processor_spec={"processor": "ocr_v1"},
        idempotency_key="wf-start-runtime-error",
        ctx=demo_admin_context(),
    )
    replay = foundry.operations.start_media_processing_workflow(
        media_item_version_id="miv-failed",
        processor_spec={"processor": "ocr_v1"},
        idempotency_key="wf-start-runtime-error",
        ctx=demo_admin_context(),
    )

    assert run["status"] == "failed"
    assert run["error"] is not None
    assert run["error"]["kind"] == "unknown"
    assert run["error"]["details"] == {"errorType": "RuntimeError"}
    assert replay["status"] == "failed"
    assert adapter.starts == 1


def _foundry(tmp_path: Path, adapter: WorkflowAdapter) -> FoundryLite:
    deps = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLite(dependencies=replace(deps, workflow_adapter=adapter))
