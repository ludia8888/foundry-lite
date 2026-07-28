from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

import pytest
from foundry_lite.application.ports.pipeline_execution_repository import PipelinePreviewRunRow
from foundry_lite.application.services.pipeline_preview_executor import PreviewExecutionResult
from foundry_lite.application.services.pipeline_preview_service import (
    PipelinePreviewService,
    _require_valid_preview_graph,
)
from foundry_lite.application.state_transitions import PIPELINE_PREVIEW_CANCELLED
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class _Engine:
    @contextmanager
    def begin(self):
        yield object()


class _Policy:
    def require(self, _ctx: RequestContext, _permission: str) -> None:
        return None


class _Runtime:
    def __init__(self) -> None:
        self.audits: list[dict[str, object]] = []

    def _require_or_audit(self, *_args: object) -> None:
        return None

    def _audit(self, *_args: object, **kwargs: object) -> None:
        self.audits.append(dict(kwargs))


class _PreviewRepository:
    def __init__(
        self,
        row: PipelinePreviewRunRow | None,
        *,
        terminal_result: PipelinePreviewRunRow | None = None,
    ) -> None:
        self.row = row
        self.terminal_result = terminal_result
        self.cancel_requests = 0

    def preview_by_id(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return self.row

    def claim_preview(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return None

    def request_preview_cancel(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        self.cancel_requests += 1
        return None

    def update_preview_terminal(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return self.terminal_result


class _PipelineRepository:
    def branch_by_id(self, **_kwargs: object) -> None:
        return None


def _row(status: str = "QUEUED") -> PipelinePreviewRunRow:
    return {
        "id": "preview-1",
        "tenant_id": "tenant-a",
        "pipeline_id": "pipeline-1",
        "branch_id": "branch-1",
        "status": status,
        "graph": {"schemaVersion": 2, "nodes": [], "edges": []},
        "graph_fingerprint": "sha256:graph",
        "target_node_id": None,
        "limits": {"tableRows": 10},
        "outputs": [],
        "artifacts": [],
        "idempotency_key": "preview-key",
        "request_fingerprint": "sha256:request",
        "is_commit_forbidden": True,
        "cancel_requested_at": None,
        "error": None,
        "created_by": "operator",
        "created_at": "2026-07-28T00:00:00Z",
        "started_at": None,
        "completed_at": None,
    }


def _service(repository: _PreviewRepository) -> PipelinePreviewService:
    service = object.__new__(PipelinePreviewService)
    service.engine = cast(Any, _Engine())
    service.policy = cast(Any, _Policy())
    service.runtime_service = cast(Any, _Runtime())
    service.pipeline_execution_repository = cast(Any, repository)
    service.pipeline_repository = cast(Any, _PipelineRepository())
    return service


def test_pipeline_preview_service_not_found_boundaries_and_invalid_graph() -> None:
    service = _service(_PreviewRepository(None))
    ctx = RequestContext()

    invalid_graph = {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "source",
                "kind": "source",
                "descriptorId": "source.dataset",
                "specVersion": 1,
                "config": {"datasetRef": "raw.orders"},
            }
        ],
        "edges": [],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }
    with pytest.raises(ValidationFailed, match="graph is invalid"):
        _require_valid_preview_graph(invalid_graph)
    with pytest.raises(NotFound, match="preview run"):
        service.get_preview_run("missing", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline branch"):
        service._branch("missing", ctx)
    with pytest.raises(NotFound, match="preview run"):
        service._require_preview(object(), ctx, "missing")


def test_pipeline_preview_cancel_is_idempotent_for_terminal_and_concurrent_rows() -> None:
    terminal_repository = _PreviewRepository(_row("SUCCEEDED"))
    terminal_service = _service(terminal_repository)

    terminal = terminal_service.cancel_preview_run("preview-1", ctx=RequestContext())

    assert terminal["status"] == "SUCCEEDED"
    assert terminal_repository.cancel_requests == 0

    queued_repository = _PreviewRepository(_row("QUEUED"))
    queued_service = _service(queued_repository)
    queued = queued_service.cancel_preview_run("preview-1", ctx=RequestContext())

    assert queued["status"] == "QUEUED"
    assert queued_repository.cancel_requests == 1
    assert cast(_Runtime, queued_service.runtime_service).audits[0]["event_type"] == (
        "pipeline.preview.cancel_requested"
    )


def test_pipeline_preview_terminal_update_reloads_concurrent_winner() -> None:
    repository = _PreviewRepository(_row("CANCEL_REQUESTED"))
    service = _service(repository)

    payload = service._complete_preview(
        RequestContext(),
        _row("CANCEL_REQUESTED"),
        PIPELINE_PREVIEW_CANCELLED,
        PreviewExecutionResult([], []),
        None,
    )

    assert payload["status"] == "CANCEL_REQUESTED"


def test_pipeline_preview_cancel_requested_execution_completes_without_running_graph() -> None:
    cancelled = _row("CANCELLED")
    repository = _PreviewRepository(_row("CANCEL_REQUESTED"), terminal_result=cancelled)
    service = _service(repository)

    payload = service.execute_preview_run("preview-1", ctx=RequestContext())

    assert payload["status"] == "CANCELLED"
