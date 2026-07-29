from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from foundry_lite.application.ports.pipeline_execution_repository import PipelineRunRow
from foundry_lite.application.services.pipeline_run_requests import (
    cancel_request_fingerprint,
    require_idempotent_cancel,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.routers import pipelines as pipeline_routes


class _AsyncPipelineFacade:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []

    def start_async(self, pipeline_id: str, **kwargs: object) -> dict[str, object]:
        self.start_calls.append({"pipelineId": pipeline_id, **kwargs})
        return {"id": "run-a", "pipelineId": pipeline_id, "status": "queued", "lastSequence": 1}

    def list_runs(self, pipeline_id: str, **_kwargs: object) -> dict[str, object]:
        return {"items": [{"id": "run-a", "pipelineId": pipeline_id, "status": "queued"}], "nextCursor": None}

    def get_run(self, run_id: str, **_kwargs: object) -> dict[str, object]:
        return {"id": run_id, "status": "cancelled", "lastSequence": 8}

    def events(self, run_id: str, *, after_sequence: int, **_kwargs: object) -> dict[str, object]:
        events = [{"id": 8, "event": "pipeline.run.cancelled", "data": {"runId": run_id}}] if after_sequence < 8 else []
        return {"events": events, "lastSequence": 8}

    def request_cancel(self, run_id: str, **kwargs: object) -> dict[str, object]:
        self.cancel_calls.append({"runId": run_id, **kwargs})
        return {"id": run_id, "status": "cancelling", "cancelReason": kwargs.get("reason")}


def test_start_defaults_to_202_queued_without_waiting_in_api_process(monkeypatch) -> None:
    pipelines = _AsyncPipelineFacade()
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(pipelines=pipelines))
    client = TestClient(api_main.app)

    response = client.post(
        "/api/pipelines/pipeline-a/runs",
        json={},
        headers={**_headers(), "Idempotency-Key": "run-key"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert pipelines.start_calls[0]["wait_seconds"] == 0


def test_list_cancel_and_last_event_id_resume_contract(monkeypatch) -> None:
    pipelines = _AsyncPipelineFacade()
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(pipelines=pipelines))
    client = TestClient(api_main.app)

    listed = client.get("/api/pipelines/pipeline-a/runs?limit=10", headers=_headers())
    cancelled = client.post(
        "/api/pipelines/runs/run-a/cancel",
        json={"reason": "operator stop"},
        headers={**_headers(), "Idempotency-Key": "cancel-key"},
    )
    events = client.get(
        "/api/pipelines/runs/run-a/events",
        headers={**_headers(), "Last-Event-ID": "7"},
    )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == "run-a"
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelling"
    assert pipelines.cancel_calls[0]["idempotency_key"] == "cancel-key"
    assert "id: 8\n" in events.text
    assert "event: pipeline.run.cancelled\n" in events.text


def test_wait_seconds_is_bounded_by_public_contract(monkeypatch) -> None:
    pipelines = _AsyncPipelineFacade()
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(pipelines=pipelines))
    client = TestClient(api_main.app)

    response = client.post(
        "/api/pipelines/pipeline-a/runs?waitSeconds=31",
        json={},
        headers={**_headers(), "Idempotency-Key": "run-key"},
    )

    assert response.status_code == 422
    assert pipelines.start_calls == []


def test_cancel_idempotency_key_rejects_a_different_reason() -> None:
    row = cast(
        PipelineRunRow,
        {
            "id": "run-a",
            "cancel_idempotency_key": "cancel-key",
            "cancel_request_fingerprint": cancel_request_fingerprint("run-a", "first reason"),
        },
    )

    require_idempotent_cancel(
        row,
        "cancel-key",
        cancel_request_fingerprint("run-a", "first reason"),
    )
    with pytest.raises(ConflictDetected):
        require_idempotent_cancel(
            row,
            "cancel-key",
            cancel_request_fingerprint("run-a", "different reason"),
        )


def test_pipeline_event_cursor_and_heartbeat_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pipeline_routes._event_sequence(None) == 0
    assert pipeline_routes._event_sequence("-7") == 0
    assert pipeline_routes._event_sequence("invalid") == 0

    class _StreamingPipelines:
        def __init__(self) -> None:
            self.event_calls = 0

        def events(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            self.event_calls += 1
            if self.event_calls == 1:
                return {"events": ["invalid-event"]}
            return {"events": []}

        def get_run(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"status": "running"}

    class _ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def no_sleep(_seconds: float) -> None:
        return None

    pipelines = _StreamingPipelines()
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(pipelines=pipelines))
    monkeypatch.setattr(pipeline_routes.asyncio, "sleep", no_sleep)

    async def scenario() -> None:
        stream = pipeline_routes._pipeline_event_stream(
            cast(Request, _ConnectedRequest()),
            "run-a",
            0,
            RequestContext(tenant_id="tenant-a"),
        )
        assert await anext(stream) == ": heartbeat\n\n"
        await stream.aclose()

    asyncio.run(scenario())
    assert pipelines.event_calls == 16


def test_pipeline_event_stream_stops_after_disconnect() -> None:
    class _DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    async def scenario() -> None:
        stream = pipeline_routes._pipeline_event_stream(
            cast(Request, _DisconnectedRequest()),
            "run-a",
            0,
            RequestContext(tenant_id="tenant-a"),
        )
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def _headers() -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "user-a",
        "X-Roles": "admin",
    }
