from __future__ import annotations

from foundry_lite.application.services.aip.fde_mcp_security import _execution_error
from foundry_lite.application.services.mcp_tool_results import tool_error_structured
from foundry_lite.application.services.runtime_error_payloads import record_runtime_operations_evidence
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite_api.errors import _handle_error, _websocket_error
from starlette.requests import Request


def test_http_and_websocket_error_boundaries_scrub_message_and_nested_details() -> None:
    exc = ValidationFailed(
        "Authorization: Bearer raw-http-token",
        details={"error": "password=raw-http-password", "apiKey": "raw-api-key"},
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.request_id = "req-error-redaction"

    http_detail = _handle_error(exc, request).detail
    websocket_detail = _websocket_error(exc, "req-error-redaction")

    assert http_detail["message"] == "***MASKED***"
    assert http_detail["details"] == {"error": "***MASKED***", "apiKey": "***MASKED***"}
    assert websocket_detail["message"] == "***MASKED***"
    assert websocket_detail["details"] == http_detail["details"]
    assert "raw-http" not in str((http_detail, websocket_detail))


def test_http_error_boundary_includes_durable_operations_evidence_coordinates() -> None:
    exc = ValidationFailed("source preview failed", details={"password": "private"})
    record_runtime_operations_evidence(
        exc,
        run_type="source_exploration",
        run_id="source_explore_failed",
    )

    detail = _handle_error(exc).detail

    assert detail["details"] == {
        "password": "***MASKED***",
        "operationsEvidence": {
            "runType": "source_exploration",
            "runId": "source_explore_failed",
            "operationsPath": "/api/operations/runs/source_exploration/source_explore_failed",
        },
    }


def test_mcp_public_and_durable_error_boundaries_scrub_secret_bearing_values() -> None:
    ctx = RequestContext(request_id="req-mcp-error-redaction")
    exc = ValidationFailed(
        "Authorization: Bearer raw-mcp-token",
        details={"error": "client_secret=raw-client-secret", "token": "raw-token"},
    )

    public_error = tool_error_structured(exc, request_id=ctx.request_id)
    durable_error = _execution_error(ctx, exc)

    assert public_error["error"]["message"] == "***MASKED***"
    assert public_error["error"]["details"] == {"error": "***MASKED***", "token": "***MASKED***"}
    assert durable_error["detail"] == "***MASKED***"
    assert durable_error["mcpToolResult"] == public_error
    assert "raw-mcp" not in str((public_error, durable_error))
