"""MCP is JSON-RPC 2.0, so the envelope is fixed and the payload is not.

Palantir MCP implements the Model Context Protocol, and the protocol draws the line for us: the
envelope has exactly four members, while `params` is whatever the addressed method takes. For
`tools/call` that is tool-specific by design, which is why each tool publishes an `inputSchema`
and the broker validates arguments against it. These tests pin that split -- the envelope is
typed here, and the payload is deliberately left for the schema its tool declared.
"""

from __future__ import annotations

import pytest
from foundry_lite_api.mcp_envelope import JsonRpcEnvelope
from pydantic import ValidationError


def test_a_well_formed_request_parses() -> None:
    envelope = JsonRpcEnvelope.model_validate(
        {"jsonrpc": "2.0", "method": "tools/call", "id": 7, "params": {"name": "ontology.inspect"}}
    )

    assert (envelope.method, envelope.id) == ("tools/call", 7)
    assert envelope.params == {"name": "ontology.inspect"}
    assert envelope.is_notification is False


def test_a_notification_has_no_id() -> None:
    """JSON-RPC uses the absent id to mean "no reply expected"; MCP relies on it for initialized."""
    envelope = JsonRpcEnvelope.model_validate({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert envelope.is_notification is True
    assert envelope.params == {}


def test_explicit_null_id_is_not_a_notification() -> None:
    envelope = JsonRpcEnvelope.model_validate({"jsonrpc": "2.0", "method": "ping", "id": None, "params": {}})

    assert envelope.has_explicit_id is True
    assert envelope.is_notification is False


@pytest.mark.parametrize(
    "payload",
    [
        {"method": "tools/list"},
        {"jsonrpc": "1.0", "method": "tools/list"},
        {"jsonrpc": "2.0"},
        {"jsonrpc": "2.0", "method": 7},
        {"jsonrpc": "2.0", "method": "tools/call", "params": "not-an-object"},
        {"jsonrpc": "2.0", "method": "tools/call", "id": {"nested": True}},
        {"jsonrpc": "2.0", "method": "tools/call", "id": True},
    ],
)
def test_a_malformed_envelope_is_refused(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JsonRpcEnvelope.model_validate(payload)


def test_params_are_not_inspected_beyond_being_an_object() -> None:
    """Rejecting an unknown key here would break every tool whose schema the envelope cannot know.

    The broker validates arguments against the `inputSchema` the tool published, which is where
    the meaning lives; the envelope only has to establish that there is an object to hand over.
    """
    envelope = JsonRpcEnvelope.model_validate(
        {"jsonrpc": "2.0", "method": "tools/call", "id": "1", "params": {"anything": {"deeply": ["nested"]}}}
    )

    assert envelope.params == {"anything": {"deeply": ["nested"]}}


def test_an_unknown_envelope_member_is_ignored_rather_than_rejected() -> None:
    """A client that adds a member is not attacking anything, and refusing would break it."""
    envelope = JsonRpcEnvelope.model_validate({"jsonrpc": "2.0", "method": "tools/list", "extra": "ignored"})

    assert envelope.method == "tools/list"
    assert not hasattr(envelope, "extra")
