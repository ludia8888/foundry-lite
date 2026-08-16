from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import Request
from foundry_lite.application.services.runtime_error_payloads import record_runtime_cleanup_failure
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite_api import mcp_internal_error_observability as observability
from foundry_lite_api.mcp_envelope import JsonRpcEnvelope
from foundry_lite_api.routers import builder_mcp, ontology_mcp, release_mcp


def test_mcp_internal_failure_log_keeps_trace_and_redacts_exception_messages(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, object]]] = []
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp/builder/app-1",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )
    request.state.request_id = "req-mcp-internal"
    try:
        raise RuntimeError("Authorization: Bearer raw-mcp-token")
    except RuntimeError as exc:
        primary = exc
    record_runtime_cleanup_failure(
        primary,
        operation="mcpRunFailureRecord",
        cleanup_error=RuntimeError("password=secondary-secret"),
    )

    def capture(_logger, event, **fields):  # type: ignore[no-untyped-def]
        events.append((event, fields))

    monkeypatch.setattr(observability, "log_event", capture)

    observability.log_mcp_internal_failure(
        logging.getLogger("mcp-test"),
        request,
        plane="builder",
        rpc_method="tools/call",
        exc=primary,
    )

    assert events[0][0] == "mcp.request.internal_failure"
    fields = events[0][1]
    assert fields["level"] == logging.ERROR
    assert fields["request_id"] == "req-mcp-internal"
    assert fields["plane"] == "builder"
    assert fields["rpc_method"] == "tools/call"
    assert fields["error_type"] == "RuntimeError"
    assert fields["cleanup_failures"] == [
        {"operation": "mcpRunFailureRecord", "status": "FAILED", "exceptionType": "RuntimeError"}
    ]
    assert fields["stack"]
    assert "raw-mcp-token" not in str(events)
    assert "secondary-secret" not in str(events)


@pytest.mark.parametrize(
    ("module", "handler_name", "plane"),
    [
        (builder_mcp, "_builder_mcp_post_response", "builder"),
        (ontology_mcp, "_ontology_mcp_post_response", "ontology"),
        (release_mcp, "_release_mcp_post_response", "release"),
    ],
)
def test_every_mcp_plane_logs_unexpected_dispatch_failure_before_safe_jsonrpc_response(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    handler_name: str,
    plane: str,
) -> None:
    request = _request("req-mcp-router")
    payload = JsonRpcEnvelope(jsonrpc="2.0", id="rpc-1", method="tools/call", params={})
    captured: list[dict[str, object]] = []
    failure = RuntimeError("Authorization: Bearer must-not-reach-response")

    monkeypatch.setattr(module, "validate_mcp_message", lambda _payload: None)
    monkeypatch.setattr(module, "_request_session_id", lambda *_args: f"{plane}-session")
    monkeypatch.setattr(module, "_dispatch", lambda *_args: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        module,
        "log_mcp_internal_failure",
        lambda _logger, _request, **fields: captured.append(fields),
    )

    handler = getattr(module, handler_name)
    response = handler("app-1", request, RequestContext(request_id="req-mcp-router"), payload)

    assert response.status_code == 200
    assert b"Internal error" in response.body
    assert b"must-not-reach-response" not in response.body
    assert captured == [{"plane": plane, "rpc_method": "tools/call", "exc": failure}]


@pytest.mark.parametrize(
    ("module", "release_method", "invalid_sequence"),
    [
        (builder_mcp, "release_fde_mcp_session_stream", True),
        (builder_mcp, "release_fde_mcp_session_stream", 0),
        (ontology_mcp, "release_session_stream", True),
        (ontology_mcp, "release_session_stream", -1),
        (release_mcp, "release_release_mcp_session_stream", True),
        (release_mcp, "release_release_mcp_session_stream", 0),
    ],
)
def test_every_mcp_plane_rejects_non_positive_or_boolean_event_sequence_and_releases_stream_lease(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    release_method: str,
    invalid_sequence: object,
) -> None:
    released: list[tuple[object, ...]] = []
    owner = getattr(module.runtime.foundry, "ontology_mcp" if module is ontology_mcp else "aip")
    if module is release_mcp:
        owner = module.runtime.foundry.release
    monkeypatch.setattr(owner, release_method, lambda *args, **kwargs: released.append((*args, kwargs)))
    generator = module._session_events(  # type: ignore[attr-defined]
        [{"sequence": invalid_sequence, "event_type": "notifications/test", "payload_json": {}}],
        "mcp-session-1",
        RequestContext(request_id="req-mcp-stream"),
        "app-1",
        "lease-1",
    )

    async def consume_once() -> str:
        try:
            return await anext(generator)
        finally:
            await generator.aclose()

    with pytest.raises(ValidationFailed, match="event sequence is invalid"):
        asyncio.run(consume_once())

    assert len(released) == 1


@pytest.mark.parametrize("module", [builder_mcp, release_mcp])
def test_mcp_resource_routers_use_standard_resource_not_found_jsonrpc_code(module: object) -> None:
    error = module._rpc_error(  # type: ignore[attr-defined]
        "resource-read",
        NotFound("MCP resource missing", details={"uri": "ui://missing"}),
        _request("req-resource-missing"),
    )

    assert error["error"]["code"] == -32002  # type: ignore[index]


def _request(request_id: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp/test/app-1",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )
    request.state.request_id = request_id
    return request
