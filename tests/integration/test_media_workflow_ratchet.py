"""L5 media Temporal workflow ratchet + the workflow-status-vs-domain-commit invariant.

The Media Processing workflow is the first product-driven Temporal use case. It
*orchestrates* one ``MediaProcessingService.process``, but the DB COMMITTED media
derivative remains the only success signal (matrix rule
``workflow_status_does_not_replace_domain_commit``). Two layers:

- ``test_media_workflow_status_does_not_replace_domain_commit`` (deterministic, runs in
  the coverage lane): proves the relationship between a ``workflow_runs`` status and
  ``resolve_derivative`` directly — a SUCCEEDED workflow run never makes an uncommitted /
  failed-processing derivative resolvable, and a committed derivative is still served when
  the workflow status is failed/unknown (the DB commit wins).
- ``test_media_processing_workflow_runs_through_temporal_and_commits`` (time-skipping
  Temporal server, mirroring the existing temporal ratchet harness): a real
  ``MediaProcessingWorkflow`` drives a real ``MediaProcessingService.process``, commits the
  derivative, records the ``workflow_runs`` + ``media_processing_runs`` rows, and the
  derivative is resolvable through the committed-only serving path.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import tempfile
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; CI/dev are POSIX.
    fcntl = None  # type: ignore[assignment]

import pytest
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
    ProcessorSpec,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import NotFound
from foundry_lite.infrastructure.adapters import FakeWorkflowAdapter
from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
)
from foundry_lite.infrastructure.adapters.temporal_workflows import (
    FOUNDRY_TASK_QUEUE,
    MEDIA_PROCESSING_WORKFLOW_NAME,
    ConnectorSyncWorkflow,
    FoundryWorkflow,
    MediaProcessingActivities,
    MediaProcessingWorkflow,
    foundry_sandbox_runner,
    run_workflow_step,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

_SPEC = ProcessorSpec(processor="ocr_v1", processor_version="1.0", model="tesseract", model_version="5.3.0")
_SPEC_PAYLOAD: dict[str, object] = {
    "processor": _SPEC.processor,
    "processorVersion": _SPEC.processor_version,
    "model": _SPEC.model,
    "modelVersion": _SPEC.model_version,
}
_TEMPORAL_TEST_SERVER_LOCK = "foundry-lite-temporal-test-server-download.lock"


class _OkProcessor:
    profile_name = "fake-ok"

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        unit = ProcessedContentUnit(unit_kind="page", ordinal=0, page_number=1, text="ocr text", text_hash="h-1")
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind="ocr_v1",
            content_hash="content-1",
            units=(unit,),
        )


class _FailingProcessor:
    profile_name = "fake-fail"

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        raise AdapterError(AdapterFailure("fake-fail", "process", "validation", False, "undecodable_image"))


def _foundry(storage_root: Path, *, processor: object, workflow_adapter: object) -> FoundryLite:
    dependencies = create_local_core_dependencies(storage_root=storage_root)
    swapped: CoreDependencies = replace(dependencies, media_processor=processor, workflow_adapter=workflow_adapter)
    return FoundryLite(dependencies=swapped)


def _committed_version_id(foundry: FoundryLite, ctx: RequestContext) -> str:
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    tx = foundry.media.open_transaction(ctx, media_set_id=media_set.media_set_id, idempotency_key="idem-1")
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=tx,
        logical_path="/a.pdf",
        source=io.BytesIO(b"%PDF-1.4 source"),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
    )
    foundry.media.commit(ctx, media_transaction_id=tx)
    return staged.media_item_version_id


# --- the matrix promotion target: workflow status is never the serving truth -------------


def test_media_workflow_status_does_not_replace_domain_commit(tmp_path: Path) -> None:
    ctx = demo_admin_context()

    # (a) processing FAILED but the workflow run reports SUCCEEDED: the recorded FAILED
    # derivative is still not resolvable — resolve returns only a COMMITTED derivative.
    failed = _foundry(tmp_path / "failed", processor=_FailingProcessor(), workflow_adapter=FakeWorkflowAdapter())
    failed_version = _committed_version_id(failed, ctx)
    failed_run = failed.operations.start_media_processing_workflow(
        media_item_version_id=failed_version,
        processor_spec=_SPEC_PAYLOAD,
        idempotency_key="media-wf-failed",
        ctx=ctx,
    )
    failed_outcome = failed.media.process(ctx, media_item_version_id=failed_version, spec=_SPEC)
    assert failed_run["status"] == "succeeded"  # orchestration reports completed
    assert failed_outcome.status == "failed"  # domain did NOT commit
    with pytest.raises(NotFound):  # workflow "succeeded" is not a domain commit
        failed.media.resolve_derivative(ctx, media_derivative_id=failed_outcome.media_derivative_id)

    # (b) the derivative committed but the workflow run is failed/unknown: the DB commit
    # wins — the committed derivative is STILL served regardless of the workflow status.
    committed = _foundry(tmp_path / "ok", processor=_OkProcessor(), workflow_adapter=_FailedStartWorkflowAdapter())
    committed_version = _committed_version_id(committed, ctx)
    committed_outcome = committed.media.process(ctx, media_item_version_id=committed_version, spec=_SPEC)
    committed_run = committed.operations.start_media_processing_workflow(
        media_item_version_id=committed_version,
        processor_spec=_SPEC_PAYLOAD,
        idempotency_key="media-wf-committed",
        ctx=ctx,
    )
    assert committed_outcome.status == "committed"
    assert committed_run["status"] in {"failed", "start_unknown"}  # orchestration not successful
    derivative = committed.media.resolve_derivative(ctx, media_derivative_id=committed_outcome.media_derivative_id)
    assert derivative.status == "COMMITTED"  # DB COMMITTED version is the only serving truth


class _FailedStartWorkflowAdapter:
    """WorkflowAdapter whose start always fails (terminal), to prove a failed workflow
    status never un-commits an already-committed derivative."""

    profile_name = "failed-start"

    def failure_contract(self) -> object:
        return FakeWorkflowAdapter().failure_contract()

    def start_workflow(self, request: object) -> object:
        from foundry_lite.application.ports.workflow_adapter import WorkflowRun, workflow_run_id

        typed = cast(Any, request)
        return WorkflowRun(
            run_id=workflow_run_id(typed),
            workflow_name=typed.workflow_name,
            status="failed",
            output={},
            error={"kind": "unknown", "retryable": False},
        )

    def workflow_run(self, tenant_id: str, run_id: str) -> object | None:
        return None


# --- time-skipping live media workflow ---------------------------------------------------


@contextmanager
def _temporal_test_server_download_lock(lock_path: Path | None = None):
    """Serialize Temporal SDK test-server downloads across xdist workers."""
    if fcntl is None:
        yield
        return
    path = lock_path or Path(tempfile.gettempdir()) / _TEMPORAL_TEST_SERVER_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _clear_stale_temporal_downloads() -> None:
    for stale in Path(tempfile.gettempdir()).glob("temporal-test-server-*.downloading"):
        with contextlib.suppress(OSError):
            stale.unlink()


@pytest.fixture(scope="module", autouse=True)
def _temporal_test_server_warm() -> None:
    async def _start_stop() -> None:
        async with await WorkflowEnvironment.start_time_skipping():
            return

    with _temporal_test_server_download_lock():
        last_error: Exception | None = None
        for _ in range(6):
            _clear_stale_temporal_downloads()
            try:
                asyncio.run(_start_stop())
                return
            except RuntimeError as exc:  # noqa: PERF203 - retry a slow cold download
                last_error = exc
        if last_error is not None:
            raise last_error


@asynccontextmanager
async def _media_harness(driver: Callable[[dict[str, Any]], dict[str, Any]]):
    """Boot a time-skipping server + worker hosting the media workflow, yield the adapter."""
    with _temporal_test_server_download_lock():
        env_context = await WorkflowEnvironment.start_time_skipping()
    async with env_context as env:
        async with Worker(
            env.client,
            task_queue=FOUNDRY_TASK_QUEUE,
            workflows=[FoundryWorkflow, ConnectorSyncWorkflow, MediaProcessingWorkflow],
            activities=[run_workflow_step, MediaProcessingActivities(driver).run_media_processing_step],
            workflow_runner=foundry_sandbox_runner(),
        ):
            adapter = TemporalWorkflowAdapter(
                TemporalWorkflowAdapterConfig(task_queue=FOUNDRY_TASK_QUEUE), client=env.client
            )
            yield env, adapter


def _run(body: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    return asyncio.run(body())


@pytest.mark.integration_scenario("media-workflow-temporal")
def test_media_processing_workflow_runs_through_temporal_and_commits(tmp_path: Path) -> None:
    async def body() -> None:
        ctx = demo_admin_context()
        foundry = _foundry(tmp_path / "live", processor=_OkProcessor(), workflow_adapter=FakeWorkflowAdapter())
        version_id = _committed_version_id(foundry, ctx)

        def driver(payload: dict[str, Any]) -> dict[str, Any]:
            # The activity drives the real domain commit; the COMMITTED derivative — not
            # this returned mapping — is the serving truth.
            outcome = foundry.media.process(ctx, media_item_version_id=str(payload["mediaItemVersionId"]), spec=_SPEC)
            return {"mediaDerivativeId": outcome.media_derivative_id, "status": outcome.status}

        async with _media_harness(driver) as (_env, adapter):
            from foundry_lite.application.ports.workflow_adapter import WorkflowStartRequest

            request = WorkflowStartRequest(
                workflow_name=MEDIA_PROCESSING_WORKFLOW_NAME,
                tenant_id=ctx.tenant_id,
                request_id=ctx.request_id,
                idempotency_key="media-wf-live",
                input={"mediaItemVersionId": version_id, "processorSpec": _SPEC_PAYLOAD},
            )
            run = await adapter.start_workflow_async(request)

        assert run.status == "succeeded"
        derivative_id = str(run.output["mediaDerivativeId"])
        assert run.output["status"] == "committed"

        # The workflow drove the commit; the run ledger + media run row are evidence...
        runs = foundry.media.list_media_runs(ctx, source_media_item_version_id=version_id)
        assert [media_run.status for media_run in runs] == ["SUCCEEDED"]
        assert runs[0].media_derivative_id == derivative_id
        # ...but the serving truth is the DB COMMITTED derivative, resolvable on its own.
        derivative = foundry.media.resolve_derivative(ctx, media_derivative_id=derivative_id)
        assert derivative.status == "COMMITTED"

    _run(body)
