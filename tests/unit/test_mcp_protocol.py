from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite_api.mcp_envelope import JsonRpcEnvelope
from foundry_lite_api.mcp_protocol import (
    SUPPORTED_PROTOCOL_VERSION,
    require_mcp_protocol_version,
    require_mcp_session_id,
    validate_mcp_message,
)
from foundry_lite_api.routers.builder_mcp import _json_body as parse_builder_body
from foundry_lite_api.routers.ontology_mcp import _json_body as parse_ontology_body
from starlette.requests import Request


def test_missing_post_initialize_header_resolves_to_the_negotiated_version() -> None:
    """Regression: rejecting a missing header made ChatGPT's first post-initialize call 400.

    The transport spec reserves the MUST-400 for a header that is present and unsupported,
    and prefers a server to rely on the version negotiated during initialization when the
    header is absent. This server only ever negotiates SUPPORTED_PROTOCOL_VERSION.
    """

    request = Request({"type": "http", "headers": []})

    assert require_mcp_protocol_version(request) == SUPPORTED_PROTOCOL_VERSION
    assert require_mcp_protocol_version(request, is_initialization=True) is None


def test_present_but_unsupported_protocol_version_is_still_rejected() -> None:
    request = Request({"type": "http", "headers": [(b"mcp-protocol-version", b"1900-01-01")]})

    with pytest.raises(ValidationFailed):
        require_mcp_protocol_version(request)
    with pytest.raises(ValidationFailed):
        require_mcp_protocol_version(request, is_initialization=True)


def test_initialize_negotiates_supported_and_latest_alternative_versions() -> None:
    supported = _initialize(SUPPORTED_PROTOCOL_VERSION)
    unknown = _initialize("2099-01-01")

    assert validate_mcp_message(supported) == SUPPORTED_PROTOCOL_VERSION
    assert validate_mcp_message(unknown) == SUPPORTED_PROTOCOL_VERSION


@pytest.mark.parametrize("session_id", [b"session id", b"x" * 256, b"session\x7f"])
def test_session_id_requires_bounded_visible_ascii(session_id: bytes) -> None:
    request = Request({"type": "http", "headers": [(b"mcp-session-id", session_id)]})

    with pytest.raises(ValidationFailed):
        require_mcp_session_id(request, "Builder")


@pytest.mark.anyio
@pytest.mark.parametrize("parse_body", [parse_builder_body, parse_ontology_body])
async def test_invalid_utf8_body_is_a_sanitized_validation_error(
    parse_body: Callable[[Request], Awaitable[JsonRpcEnvelope]],
) -> None:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"\xff", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp/test",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )

    with pytest.raises(ValidationFailed, match="request body must be JSON"):
        await parse_body(request)


def _initialize(protocol_version: str) -> JsonRpcEnvelope:
    return JsonRpcEnvelope.model_validate(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "wire-test", "version": "1.0.0"},
            },
        }
    )
