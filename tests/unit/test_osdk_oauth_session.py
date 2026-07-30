from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import OAuthAccessTokenClaims, OsdkApplicationBundle
from foundry_lite.application.services.osdk_oauth_session_service import _access_ttl, _require_redirect_uri
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied, RateLimited, ValidationFailed
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider, LocalOAuthTokenIssuer
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

_REDIRECT_URI = "https://orders.example.test/oauth/callback"
_READ_SCOPE = "osdk:object:Order:read"
_SUBSCRIBE_SCOPE = "osdk:object:Order:subscribe"


def test_osdk_oauth_authorize_token_refresh_revoke_and_claims(tmp_path: Path) -> None:
    foundry, verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    app = _create_app_and_client(foundry, ctx)
    verifier_text = "orders-pkce-verifier"
    challenge = _s256_challenge(verifier_text)

    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=challenge,
        code_challenge_method="S256",
        scopes=(_READ_SCOPE,),
        state="state-1",
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id="orders-web",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier_text,
        ctx=ctx,
    )

    principal = verifier.authenticate({"Authorization": f"Bearer {token['accessToken']}"})

    assert principal.tenant_id == ctx.tenant_id
    assert principal.actor_user_id == ctx.actor_user_id
    assert principal.application_id == app["application"]["id"]
    assert principal.client_id == "orders-web"
    assert principal.token_scopes == (_READ_SCOPE,)
    with pytest.raises(PermissionDenied, match="authorization code is invalid"):
        foundry.auth.osdk_oauth_token(
            client_id="orders-web",
            code=cast(str, authorized["code"]),
            redirect_uri=_REDIRECT_URI,
            code_verifier=verifier_text,
            ctx=ctx,
        )

    refreshed = foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, token["refreshToken"]), ctx=ctx)

    assert refreshed["refreshToken"] != token["refreshToken"]

    revoked = foundry.auth.osdk_oauth_revoke(refresh_token=cast(str, refreshed["refreshToken"]), ctx=ctx)

    assert revoked["status"] == "revoked"
    with pytest.raises(PermissionDenied, match="refresh token is invalid"):
        foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, refreshed["refreshToken"]), ctx=ctx)
    _assert_oauth_audit_is_raw_token_free(foundry, ctx, authorized, token, refreshed)


def test_osdk_oauth_security_runtime_rate_limit_is_retryable_and_redacted(tmp_path: Path) -> None:
    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)
    challenge = _s256_challenge("rate-limit-verifier")

    for index in range(5):
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri=_REDIRECT_URI,
            code_challenge=challenge,
            code_challenge_method="S256",
            scopes=(_READ_SCOPE,),
            state=f"state-{index}",
            ctx=ctx,
        )

    with pytest.raises(RateLimited) as exc_info:
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri=_REDIRECT_URI,
            code_challenge=challenge,
            code_challenge_method="S256",
            scopes=(_READ_SCOPE,),
            state="state-rate-limited",
            ctx=ctx,
        )

    payload = json.dumps(exc_info.value.details, sort_keys=True)

    assert exc_info.value.code == "RATE_LIMITED"
    assert isinstance(exc_info.value.details["retryAfterSeconds"], int)
    assert "oauth_code_" not in payload
    assert "refresh_" not in payload


def test_osdk_oauth_security_runtime_authorization_code_replay_is_audited(
    tmp_path: Path,
) -> None:
    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)
    verifier_text = "orders-code-replay-verifier"
    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge(verifier_text),
        code_challenge_method="S256",
        scopes=(_READ_SCOPE,),
        ctx=ctx,
    )
    foundry.auth.osdk_oauth_token(
        client_id="orders-web",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier_text,
        ctx=ctx,
    )

    with pytest.raises(PermissionDenied, match="authorization code is invalid"):
        foundry.auth.osdk_oauth_token(
            client_id="orders-web",
            code=cast(str, authorized["code"]),
            redirect_uri=_REDIRECT_URI,
            code_verifier=verifier_text,
            ctx=ctx,
        )

    audit_payload = json.dumps(foundry.operations.list_runs(ctx=ctx)["auditEvents"], sort_keys=True)

    assert "osdk.oauth.authorization_code_replay_detected" in audit_payload
    assert cast(str, authorized["code"]) not in audit_payload


def test_osdk_oauth_security_runtime_refresh_family_compromise_revokes_replacement(
    tmp_path: Path,
) -> None:
    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)
    verifier_text = "orders-family-verifier"
    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge(verifier_text),
        code_challenge_method="S256",
        scopes=(_READ_SCOPE,),
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id="orders-web",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier_text,
        ctx=ctx,
    )
    refreshed = foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, token["refreshToken"]), ctx=ctx)

    with pytest.raises(PermissionDenied, match="refresh token is invalid"):
        foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, token["refreshToken"]), ctx=ctx)
    with pytest.raises(PermissionDenied, match="refresh token is invalid"):
        foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, refreshed["refreshToken"]), ctx=ctx)

    audit_payload = json.dumps(foundry.operations.list_runs(ctx=ctx)["auditEvents"], sort_keys=True)

    assert "osdk.oauth.refresh_family_compromised" in audit_payload
    assert cast(str, token["refreshToken"]) not in audit_payload
    assert cast(str, refreshed["refreshToken"]) not in audit_payload


def test_osdk_oauth_security_runtime_local_jwks_rotation_proves_grace_and_retirement(
    tmp_path: Path,
) -> None:
    issuer = LocalOAuthTokenIssuer.from_key_path(tmp_path / "oauth-private-key.pem")
    old_token = issuer.issue_access_token(_access_claims(), ttl_seconds=900)
    rotated = issuer.rotated(rsa.generate_private_key(public_exponent=65537, key_size=2048), key_id="kid-new")
    new_token = rotated.issue_access_token(_access_claims(actor_user_id="user-new"), ttl_seconds=900)
    jwks_state: dict[str, object] = dict(issuer.public_jwks())
    clock_value = 1_000.0
    verifier = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer.issuer,
            audience=issuer.audience,
            jwks=jwks_state,
            jwks_refresh_interval_seconds=0,
            retired_key_grace_seconds=10,
        ),
        jwks_loader=lambda: jwks_state,
        clock=lambda: clock_value,
    )

    assert verifier.authenticate({"Authorization": f"Bearer {old_token['accessToken']}"}).actor_user_id == "user-osdk"
    rotated_keys = cast(list[object], rotated.public_jwks()["keys"])
    jwks_state = {"keys": [rotated_keys[0]]}
    clock_value += 1
    assert verifier.authenticate({"Authorization": f"Bearer {new_token['accessToken']}"}).actor_user_id == "user-new"
    assert verifier.authenticate({"Authorization": f"Bearer {old_token['accessToken']}"}).actor_user_id == "user-osdk"
    clock_value += 11

    with pytest.raises(PermissionDenied, match="authentication failed"):
        verifier.authenticate({"Authorization": f"Bearer {old_token['accessToken']}"})


def test_osdk_oauth_fails_closed_for_redirect_pkce_scope_and_inactive_client(tmp_path: Path) -> None:
    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    app = _create_app_and_client(foundry, ctx)

    with pytest.raises(PermissionDenied, match="redirect URI"):
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri="https://evil.example.test/callback",
            code_challenge=_s256_challenge("placeholder"),
            scopes=(_READ_SCOPE,),
            ctx=ctx,
        )
    with pytest.raises(PermissionDenied, match="requested scope"):
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri=_REDIRECT_URI,
            code_challenge=_s256_challenge("placeholder"),
            scopes=("osdk:action:ApproveOrder:execute",),
            ctx=ctx,
        )

    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge("correct-verifier"),
        scopes=(_READ_SCOPE,),
        ctx=ctx,
    )
    with pytest.raises(PermissionDenied, match="PKCE"):
        foundry.auth.osdk_oauth_token(
            client_id="orders-web",
            code=cast(str, authorized["code"]),
            redirect_uri=_REDIRECT_URI,
            code_verifier="wrong-verifier",
            ctx=ctx,
        )

    client_row = foundry.developer_console.list_osdk_application_clients(cast(str, app["application"]["id"]), ctx=ctx)[
        0
    ]
    foundry.developer_console.deactivate_osdk_application_client(
        cast(str, app["application"]["id"]),
        cast(str, client_row["id"]),
        idempotency_key="deactivate-orders-web",
        ctx=ctx,
    )
    with pytest.raises(PermissionDenied, match="client is not active"):
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri=_REDIRECT_URI,
            code_challenge=_s256_challenge("placeholder"),
            scopes=(_READ_SCOPE,),
            ctx=ctx,
        )


def test_osdk_oauth_plain_pkce_is_rejected(tmp_path: Path) -> None:
    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)

    with pytest.raises(ValidationFailed, match="S256"):
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri=_REDIRECT_URI,
            code_challenge=_s256_challenge("plain-verifier"),
            code_challenge_method="plain",
            ctx=ctx,
        )


def test_osdk_oauth_rejects_malformed_code_challenge(tmp_path: Path) -> None:
    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)

    with pytest.raises(ValidationFailed, match="code_challenge"):
        foundry.auth.osdk_oauth_authorize(
            client_id="orders-web",
            redirect_uri=_REDIRECT_URI,
            code_challenge="too-short",
            ctx=ctx,
        )


def test_osdk_oauth_default_scope_narrowing(tmp_path: Path) -> None:
    foundry, verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    app = _create_app_and_client(foundry, ctx)
    verifier_text = "default-scope-verifier"

    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge(verifier_text),
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id="orders-web",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier_text,
        ctx=ctx,
    )

    principal = verifier.authenticate({"Authorization": f"Bearer {token['accessToken']}"})

    assert principal.application_id == app["application"]["id"]
    assert set(principal.token_scopes) == {_READ_SCOPE, _SUBSCRIBE_SCOPE}


def test_osdk_oauth_redirect_uri_fails_closed_when_none_registered() -> None:
    with pytest.raises(PermissionDenied, match="redirect URI"):
        _require_redirect_uri({"redirect_uris": []}, _REDIRECT_URI)
    with pytest.raises(PermissionDenied, match="redirect URI"):
        _require_redirect_uri({"redirect_uris": None}, _REDIRECT_URI)


def test_osdk_oauth_refresh_reevaluates_current_roles(tmp_path: Path) -> None:
    foundry, verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)
    verifier_text = "roles-refresh-verifier"

    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge(verifier_text),
        scopes=(_READ_SCOPE,),
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id="orders-web",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier_text,
        ctx=ctx,
    )
    reduced_ctx = RequestContext(actor_user_id=ctx.actor_user_id, roles=("data_engineer",))
    refreshed = foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, token["refreshToken"]), ctx=reduced_ctx)

    principal = verifier.authenticate({"Authorization": f"Bearer {refreshed['accessToken']}"})

    assert "admin" not in principal.roles
    assert principal.roles == ("data_engineer",)


def test_osdk_oauth_default_access_ttl_is_short() -> None:
    assert _access_ttl({}) <= 120


def _oauth_foundry(tmp_path: Path) -> tuple[FoundryLite, JwtOidcAuthProvider]:
    deps = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=deps)
    issuer = cast(Any, deps.oauth_token_issuer)
    verifier = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=cast(str, issuer.issuer),
            audience=cast(str, issuer.audience),
            jwks=issuer.public_jwks(),
        )
    )
    return foundry, verifier


def _create_app_and_client(foundry: FoundryLite, ctx: RequestContext) -> OsdkApplicationBundle:
    app = foundry.developer_console.create_osdk_application(
        app_api_name="OrdersOAuthApp",
        display_name="Orders OAuth App",
        client_id="orders-web",
        resources=({"resourceType": "object", "resourceApiName": "Order", "scopes": [_READ_SCOPE, _SUBSCRIBE_SCOPE]},),
        idempotency_key="orders-oauth-app-create",
        ctx=ctx,
    )
    foundry.developer_console.create_osdk_application_client(
        cast(str, app["application"]["id"]),
        client_id="orders-web",
        redirect_uris=(_REDIRECT_URI,),
        allowed_scopes=(_READ_SCOPE, _SUBSCRIBE_SCOPE),
        access_token_ttl_seconds=600,
        refresh_token_ttl_seconds=3600,
        idempotency_key="orders-oauth-client-create",
        ctx=ctx,
    )
    return app


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _access_claims(actor_user_id: str = "user-osdk") -> OAuthAccessTokenClaims:
    return {
        "tenant_id": "tenant-osdk",
        "actor_user_id": actor_user_id,
        "roles": ["admin", "data_engineer"],
        "application_id": "osdk_app_orders",
        "client_id": "orders-web-client",
        "scopes": ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
        "session_id": "session-1",
    }


def _assert_oauth_audit_is_raw_token_free(
    foundry: FoundryLite,
    ctx: RequestContext,
    authorized: Mapping[str, object],
    token: Mapping[str, object],
    refreshed: Mapping[str, object],
) -> None:
    payload = json.dumps(foundry.operations.list_runs(ctx=ctx)["auditEvents"], sort_keys=True)
    assert "osdk.oauth.authorization_code.created" in payload
    assert "osdk.oauth.session.created" in payload
    assert "osdk.oauth.refresh_rotated" in payload
    assert "osdk.oauth.session.revoked" in payload
    assert cast(str, authorized["code"]) not in payload
    assert cast(str, token["accessToken"]) not in payload
    assert cast(str, token["refreshToken"]) not in payload
    assert cast(str, refreshed["refreshToken"]) not in payload


def test_osdk_oauth_refresh_aborts_when_rotation_cas_loses_the_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh whose rotation CAS loses must not leave a second live token.

    Two concurrent refreshes of the same active token both read it as active
    under READ COMMITTED and both insert a replacement. The winner flips the old
    token active->rotated; the loser's CAS matches zero rows. When that CAS
    result was ignored, the loser still committed its freshly-inserted token, so
    the session ended with two active refresh tokens and reuse detection (which
    only fires when a *rotated* token is re-presented) never flagged the theft.

    Here we simulate the loser directly: force ``rotate_refresh_token`` to report
    a lost CAS. The refresh must raise and roll back, leaving exactly the one
    original active token, which still works.
    """
    from foundry_lite.infrastructure import schema as db
    from foundry_lite.infrastructure.repositories.oauth_session_repository import (
        SqlAlchemyOAuthSessionRepository,
    )

    foundry, _verifier = _oauth_foundry(tmp_path)
    ctx = demo_admin_context()
    _create_app_and_client(foundry, ctx)
    verifier_text = "orders-race-verifier"
    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-web",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge(verifier_text),
        code_challenge_method="S256",
        scopes=(_READ_SCOPE,),
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id="orders-web",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier_text,
        ctx=ctx,
    )

    def active_refresh_token_count() -> int:
        with foundry.engine.connect() as conn:
            rows = conn.execute(
                db.osdk_oauth_refresh_tokens.select().where(db.osdk_oauth_refresh_tokens.c.status == "active")
            ).all()
        return len(rows)

    assert active_refresh_token_count() == 1

    monkeypatch.setattr(SqlAlchemyOAuthSessionRepository, "rotate_refresh_token", lambda *a, **k: False)

    with pytest.raises(FoundryLiteError):
        foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, token["refreshToken"]), ctx=ctx)

    # The just-inserted replacement was rolled back: still exactly one active token.
    assert active_refresh_token_count() == 1

    # The original token remains usable once the race is not simulated.
    monkeypatch.undo()
    refreshed = foundry.auth.osdk_oauth_refresh(refresh_token=cast(str, token["refreshToken"]), ctx=ctx)
    assert refreshed["refreshToken"] != token["refreshToken"]
