"""Verify two external-OIDC release principals through the production auth adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from foundry_lite.application.ports.auth_provider import Principal
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.infrastructure.auth.oidc import (
    OIDC_ALLOWED_CLIENT_IDS_JSON_ENV,
    OIDC_AUDIENCE_ENV,
    OIDC_CLIENT_ID_CLAIM_ENV,
    OIDC_DISCOVERY_URL_ENV,
    OIDC_GRANT_TYPE_CLAIM_ENV,
    OIDC_GRANT_TYPE_VALUE_ENV,
    OIDC_HUMAN_GRANT_CLAIM_ENV,
    OIDC_HUMAN_GRANT_VALUE_ENV,
    OIDC_ISSUER_ENV,
    OIDC_SESSION_CLAIM_ENV,
    jwt_oidc_auth_provider_from_env,
)

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, utc_now, write_json_receipt

_MAX_TOKEN_BYTES = 32 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def verify(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    if _RUN_ID.fullmatch(args.run_id) is None:
        raise ValueError("macmini_oidc_run_id_invalid")
    issuer = _clean_https_url(args.issuer, "macmini_oidc_issuer_invalid")
    discovery_url = _clean_https_url(args.discovery_url, "macmini_oidc_discovery_url_invalid")
    audience = _clean_https_url(args.audience, "macmini_oidc_audience_invalid")
    if discovery_url != f"{issuer}/.well-known/openid-configuration":
        raise ValueError("macmini_oidc_discovery_issuer_mismatch")
    clients = _allowed_clients(args.allowed_client_id)
    author_token = _private_token(args.author_token_file)
    reviewer_token = _private_token(args.reviewer_token_file)
    provider = jwt_oidc_auth_provider_from_env(_provider_environment(issuer, discovery_url, audience, clients))
    author = provider.authenticate_for_audience({"authorization": f"Bearer {author_token}"}, audience)
    reviewer = provider.authenticate_for_audience({"authorization": f"Bearer {reviewer_token}"}, audience)
    _require_principal(author, issuer, audience, clients, "author")
    _require_principal(reviewer, issuer, audience, clients, "reviewer")
    _require_distinct_principals(author, reviewer)
    receipt = _receipt(issuer, audience, author, reviewer)
    target = QA_ROOT / "evidence" / args.run_id / "external-oidc-principals.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _provider_environment(
    issuer: str,
    discovery_url: str,
    audience: str,
    clients: frozenset[str],
) -> dict[str, str]:
    return {
        OIDC_ISSUER_ENV: issuer,
        OIDC_DISCOVERY_URL_ENV: discovery_url,
        OIDC_AUDIENCE_ENV: audience,
        OIDC_ALLOWED_CLIENT_IDS_JSON_ENV: json.dumps(sorted(clients)),
        OIDC_CLIENT_ID_CLAIM_ENV: "azp",
        OIDC_SESSION_CLAIM_ENV: "sid",
        OIDC_HUMAN_GRANT_CLAIM_ENV: "human_grant",
        OIDC_HUMAN_GRANT_VALUE_ENV: "true",
        OIDC_GRANT_TYPE_CLAIM_ENV: "authorization_grant_type",
        OIDC_GRANT_TYPE_VALUE_ENV: "authorization_code",
    }


def _require_principal(
    principal: Principal,
    issuer: str,
    audience: str,
    clients: frozenset[str],
    role: str,
) -> None:
    checks = (
        principal.is_human_oauth is True,
        principal.oauth_session_authority == "issuer",
        principal.authorization_server_issuer == issuer,
        isinstance(principal.oauth_session_hash, str)
        and principal.oauth_session_hash.startswith("oauth-session:sha256:"),
        principal.oauth_grant_type == "authorization_code",
        principal.oauth_resource == audience,
        principal.client_id in clients,
        GOVERNED_RELEASE_SCOPE in principal.token_scopes,
    )
    if not all(checks):
        raise RuntimeError(f"macmini_oidc_{role}_principal_invalid")


def _require_distinct_principals(author: Principal, reviewer: Principal) -> None:
    if author.tenant_id != reviewer.tenant_id:
        raise RuntimeError("macmini_oidc_principal_tenants_mismatch")
    if author.actor_user_id == reviewer.actor_user_id:
        raise RuntimeError("macmini_oidc_principal_subjects_overlap")
    if author.oauth_session_hash == reviewer.oauth_session_hash:
        raise RuntimeError("macmini_oidc_principal_sessions_overlap")
    if author.client_id != reviewer.client_id:
        raise RuntimeError("macmini_oidc_principal_clients_mismatch")


def _receipt(issuer: str, audience: str, author: Principal, reviewer: Principal) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "verifiedAt": utc_now(),
        "issuer": issuer,
        "audience": audience,
        "clientId": author.client_id,
        "tenantHash": _hash(author.tenant_id),
        "authorSubjectHash": _hash(author.actor_user_id),
        "reviewerSubjectHash": _hash(reviewer.actor_user_id),
        "authorOAuthSessionHash": author.oauth_session_hash,
        "reviewerOAuthSessionHash": reviewer.oauth_session_hash,
        "subjectsDistinct": True,
        "oauthSessionsDistinct": True,
        "authorizationCodeHumanGrant": True,
        "requiredScope": GOVERNED_RELEASE_SCOPE,
        "rawTokensStored": False,
    }


def _private_token(raw_path: str) -> str:
    resolved = _private_token_path(raw_path)
    _require_private_token_metadata(resolved)
    token = resolved.read_text(encoding="utf-8").strip()
    _require_private_token_text(token)
    return token


def _private_token_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_symlink():
        raise ValueError("macmini_oidc_token_file_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("macmini_oidc_token_file_invalid") from exc
    if resolved.parent != QA_ROOT / "state" or not resolved.is_file():
        raise ValueError("macmini_oidc_token_file_invalid")
    return resolved


def _require_private_token_metadata(resolved: Path) -> None:
    metadata = resolved.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > _MAX_TOKEN_BYTES:
        raise ValueError("macmini_oidc_token_file_invalid")


def _require_private_token_text(token: str) -> None:
    if not token or len(token) > _MAX_TOKEN_BYTES or any(character.isspace() for character in token):
        raise ValueError("macmini_oidc_token_file_invalid")


def _allowed_clients(values: Sequence[str]) -> frozenset[str]:
    clients = frozenset(value.strip() for value in values if value.strip())
    if not clients or any(len(value) > 255 for value in clients):
        raise ValueError("macmini_oidc_allowed_clients_invalid")
    return clients


def _clean_https_url(value: str, reason: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(reason)
    return value.rstrip("/")


def _hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--discovery-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--allowed-client-id", action="append", required=True)
    parser.add_argument("--author-token-file", required=True)
    parser.add_argument("--reviewer-token-file", required=True)
    verify(parser.parse_args(argv))
    print('{"rawTokensInOutput": false, "status": "passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
