"""RFC 7591 registration parsing and scope-ceiling proof for remote MCP hosts."""

from __future__ import annotations

import pytest
from foundry_lite.application.services.osdk_dynamic_client_registration import (
    _granted_scopes,
    _registration_response,
    parse_dynamic_client_registration,
)
from foundry_lite.domain.errors import NotFound, ValidationFailed

_CHATGPT_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


def test_registration_accepts_a_chatgpt_shaped_public_pkce_client() -> None:
    registration = parse_dynamic_client_registration(
        {
            "redirect_uris": [_CHATGPT_REDIRECT],
            "client_name": "ChatGPT",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "osdk:object:Order:read osdk:action:ExpediteOrder:execute",
        }
    )

    assert registration.redirect_uris == (_CHATGPT_REDIRECT,)
    assert registration.client_name == "ChatGPT"
    assert registration.requested_scopes == (
        "osdk:object:Order:read",
        "osdk:action:ExpediteOrder:execute",
    )


def test_registration_accepts_a_loopback_redirect_for_local_clients() -> None:
    registration = parse_dynamic_client_registration({"redirect_uris": ["http://127.0.0.1:33418/callback"]})

    assert registration.redirect_uris == ("http://127.0.0.1:33418/callback",)
    assert registration.client_name is None
    assert registration.requested_scopes == ()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "at least one redirect_uri"),
        ({"redirect_uris": []}, "at least one redirect_uri"),
        ({"redirect_uris": ["http://evil.example.test/cb"]}, "https or a loopback"),
        ({"redirect_uris": [f"{_CHATGPT_REDIRECT}#fragment"]}, "must not carry a fragment"),
        ({"redirect_uris": [_CHATGPT_REDIRECT], "grant_types": ["implicit"]}, "grant_types"),
        ({"redirect_uris": [_CHATGPT_REDIRECT], "response_types": ["token"]}, "response_types"),
        (
            {"redirect_uris": [_CHATGPT_REDIRECT], "token_endpoint_auth_method": "client_secret_post"},
            "token_endpoint_auth_method must be none",
        ),
    ],
)
def test_registration_rejects_requests_this_server_cannot_honour(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        parse_dynamic_client_registration(payload)


def test_granted_scopes_are_clamped_to_the_application_ceiling() -> None:
    available = ("osdk:object:Order:read", "osdk:action:ExpediteOrder:execute")

    assert _granted_scopes(available, ()) == available
    assert _granted_scopes(available, ("osdk:object:Order:read",)) == ("osdk:object:Order:read",)
    assert _granted_scopes(available, ("osdk:object:Order:read", "osdk:object:Secret:read")) == (
        "osdk:object:Order:read",
    )


def test_registration_response_omits_scope_so_every_plane_stays_reachable() -> None:
    """Regression: naming a scope pinned the client to one plane and 403'd the others.

    Hosts echo the registered scope back at authorize verbatim. A response that names the
    whole application ceiling fails the Ontology plane's subset check, and one that names a
    single plane's scopes fails every other plane. Omitting it lets authorize derive the
    right subset from the resource being requested.
    """

    registration = parse_dynamic_client_registration({"redirect_uris": [_CHATGPT_REDIRECT]})

    response = _registration_response("dcr-test", registration)

    assert "scope" not in response
    assert response["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in response


def test_granted_scopes_fail_closed_when_nothing_overlaps() -> None:
    with pytest.raises(ValidationFailed, match="no scope this application publishes"):
        _granted_scopes(("osdk:object:Order:read",), ("osdk:object:Secret:read",))

    with pytest.raises(NotFound, match="publishes no scope"):
        _granted_scopes((), ())
