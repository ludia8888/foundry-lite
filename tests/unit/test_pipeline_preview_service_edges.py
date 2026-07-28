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
from foundry_lite.application.state_transitions import PIPELINE_PREVIEW_CANCELLED, StatusTransition
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

    def _error_payload(self, exc: Exception, ctx: RequestContext, *, run_id: str) -> dict[str, object]:
        return {
            "type": type(exc).__name__,
            "message": str(exc),
            "requestId": ctx.request_id,
            "runId": run_id,
        }


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

    def reclaim_expired_preview(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return None

    def renew_preview_execution_lease(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return self.row

    def recoverable_previews(self, **_kwargs: object) -> list[PipelinePreviewRunRow]:
        return [] if self.row is None else [self.row]

    def complete_preview_success(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return self.terminal_result

    def complete_preview_failure(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        return self.terminal_result

    def request_preview_cancel(self, **_kwargs: object) -> PipelinePreviewRunRow | None:
        self.cancel_requests += 1
        if self.row is not None and self.row["status"] in {"QUEUED", "RUNNING"}:
            self.row = cast(PipelinePreviewRunRow, {**self.row, "status": "CANCEL_REQUESTED"})
            return self.row
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
        "execution_context": {
            "actorUserId": "operator",
            "roles": ["data_engineer"],
            "applicationId": None,
            "clientId": None,
            "tokenScopes": [],
        },
        "execution_lease_token": "preview-lease" if status != "QUEUED" else None,
        "execution_lease_expires_at": "9999-12-31T23:59:59Z" if status != "QUEUED" else None,
        "execution_heartbeat_at": "2026-07-28T00:00:00Z" if status != "QUEUED" else None,
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

    assert queued["status"] == "CANCEL_REQUESTED"
    assert queued_repository.cancel_requests == 1
    assert cast(_Runtime, queued_service.runtime_service).audits[0]["event_type"] == (
        "pipeline.preview.cancel_requested"
    )

    requested_repository = _PreviewRepository(_row("CANCEL_REQUESTED"))
    requested_service = _service(requested_repository)

    first_retry = requested_service.cancel_preview_run("preview-1", ctx=RequestContext())
    second_retry = requested_service.cancel_preview_run("preview-1", ctx=RequestContext())

    assert first_retry["status"] == second_retry["status"] == "CANCEL_REQUESTED"
    assert requested_repository.cancel_requests == 2
    assert cast(_Runtime, requested_service.runtime_service).audits == []


def test_pipeline_preview_terminal_update_reloads_concurrent_winner() -> None:
    repository = _PreviewRepository(_row("CANCEL_REQUESTED"))
    service = _service(repository)

    payload = service._complete_preview(
        RequestContext(),
        _row("CANCEL_REQUESTED"),
        PIPELINE_PREVIEW_CANCELLED,
        PreviewExecutionResult([], []),
        None,
        "preview-lease",
    )

    assert payload["status"] == "CANCEL_REQUESTED"


def test_pipeline_preview_success_resolves_concurrent_cancel_in_repository_transaction() -> None:
    cancelled = _row("CANCELLED")
    repository = _PreviewRepository(_row("CANCEL_REQUESTED"), terminal_result=cancelled)
    service = _service(repository)

    payload = service._complete_preview_success(
        RequestContext(),
        _row("RUNNING"),
        PreviewExecutionResult([{"kind": "table"}], []),
        "preview-lease",
    )

    assert payload["status"] == "CANCELLED"


def test_pipeline_preview_failure_resolves_concurrent_cancel_in_repository_transaction() -> None:
    cancelled = _row("CANCELLED")
    repository = _PreviewRepository(_row("CANCEL_REQUESTED"), terminal_result=cancelled)
    service = _service(repository)

    payload = service._finish_claimed_execution(
        RequestContext(),
        _row("RUNNING"),
        "preview-lease",
        PreviewExecutionResult([], []),
        {"code": "NODE_FAILED"},
    )

    assert payload["status"] == "CANCELLED"


def test_pipeline_preview_cancel_requested_execution_completes_without_running_graph() -> None:
    cancelled = _row("CANCELLED")
    repository = _PreviewRepository(_row("CANCEL_REQUESTED"), terminal_result=cancelled)
    service = _service(repository)

    payload = service.execute_preview_run("preview-1", ctx=RequestContext())

    assert payload["status"] == "CANCELLED"


def test_pipeline_preview_recovery_fails_poisoned_row_and_continues_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned = _row("QUEUED")
    poisoned["id"] = "preview-poisoned"
    healthy = _row("QUEUED")
    healthy["id"] = "preview-healthy"
    repository = _PreviewRepository(poisoned)
    service = _service(repository)
    service._recovery_cursor = cast(Any, object())
    service.metadata_repository = cast(Any, object())
    executed: list[str] = []
    completed: list[tuple[str, str, dict[str, object] | None]] = []

    monkeypatch.setattr(
        "foundry_lite.application.services.pipeline_preview_service.recoverable_pipeline_previews",
        lambda *_args, **_kwargs: [poisoned, healthy],
    )

    def _execute(preview_run_id: str, *, ctx: RequestContext) -> dict[str, object]:
        executed.append(preview_run_id)
        if preview_run_id == "preview-poisoned":
            raise ValidationFailed("persisted caller is no longer authorized")
        return {"status": "SUCCEEDED", "requestId": ctx.request_id}

    def _complete(
        _ctx: RequestContext,
        row: PipelinePreviewRunRow,
        transition: StatusTransition,
        _result: PreviewExecutionResult,
        error: dict[str, object] | None,
        _execution_lease_token: str | None,
    ) -> dict[str, object]:
        completed.append((str(row["id"]), transition.to_status, error))
        return {"status": transition.to_status}

    monkeypatch.setattr(service, "execute_preview_run", _execute)
    monkeypatch.setattr(service, "_complete_preview", _complete)

    result = service.recover_preview_runs(limit=2)

    assert result == {
        "processed": 2,
        "previewRunIds": ["preview-poisoned", "preview-healthy"],
    }
    assert executed == ["preview-poisoned", "preview-healthy"]
    assert completed[0][0:2] == ("preview-poisoned", "FAILED")
    assert completed[0][2] is not None
    assert completed[0][2]["runId"] == "preview-poisoned"
