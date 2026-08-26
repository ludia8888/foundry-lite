"""Idempotently bootstrap distinct private QA release principals."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_REALM = "foundry-lite"
_ROLES = ("admin", "data_engineer", "ops_manager", "finance", "reviewer")
_QA_PRINCIPALS = (
    ("author", "KEYCLOAK_QA_AUTHOR_USER", "KEYCLOAK_QA_AUTHOR_USER_PASSWORD"),
    ("reviewer", "KEYCLOAK_QA_REVIEWER_USER", "KEYCLOAK_QA_REVIEWER_USER_PASSWORD"),
)
_RUNTIME_SCOPE = "foundry-lite-runtime"
_RUNTIME_MAPPERS = (
    {
        "name": "foundry-lite-subject",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-sub-mapper",
        "consentRequired": False,
        "config": {
            "access.token.claim": "true",
            "lightweight.claim": "true",
            "introspection.token.claim": "true",
        },
    },
    {
        "name": "foundry-lite-realm-roles",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-hardcoded-claim-mapper",
        "consentRequired": False,
        "config": {
            "claim.name": "roles",
            "claim.value": json.dumps(list(_ROLES), separators=(",", ":")),
            "jsonType.label": "JSON",
            "access.token.claim": "true",
            "lightweight.claim": "true",
            "id.token.claim": "false",
            "userinfo.token.claim": "false",
        },
    },
)


def bootstrap(base_url: str, environment: Mapping[str, str] = os.environ) -> dict[str, object]:
    origin = _validated_origin(base_url)
    admin = _required(environment, "KEYCLOAK_ADMIN")
    admin_password = _required(environment, "KEYCLOAK_ADMIN_PASSWORD")
    principals = [
        (role, _required(environment, user_key), _required(environment, password_key))
        for role, user_key, password_key in _QA_PRINCIPALS
    ]
    if len({username for _, username, _ in principals}) != len(principals):
        raise ValueError("keycloak_bootstrap_principals_must_be_distinct")
    token = _admin_token(origin, admin, admin_password)
    results = [_bootstrap_principal(origin, token, role, username, password) for role, username, password in principals]
    _ensure_runtime_claim_mappers(origin, token)
    return {
        "schemaVersion": 2,
        "status": "created" if any(result["status"] == "created" for result in results) else "updated",
        "realm": _REALM,
        "principals": results,
        "distinctSubjectsRequired": True,
        "passwordStoredInReceipt": False,
        "tokenStoredInReceipt": False,
        "runtimeSubjectAndRolesMapped": True,
    }


def _bootstrap_principal(origin: str, token: str, role: str, username: str, password: str) -> dict[str, object]:
    user_id, was_created = _ensure_user(origin, token, username)
    _update_user_profile(origin, token, user_id, role, username)
    _reset_password(origin, token, user_id, password)
    _assign_roles(origin, token, user_id)
    return {
        "role": role,
        "username": username,
        "status": "created" if was_created else "updated",
        "roles": list(_ROLES),
    }


def _update_user_profile(origin: str, token: str, user_id: str, role: str, username: str) -> None:
    payload = json.dumps(
        {
            "username": username,
            "enabled": True,
            "firstName": "Enterprise QA",
            "lastName": role.title(),
            "email": f"{username}@example.invalid",
            "emailVerified": True,
            "requiredActions": [],
        },
        separators=(",", ":"),
    ).encode()
    path = f"/admin/realms/{_REALM}/users/{urllib.parse.quote(user_id, safe='')}"
    status, _, _ = _request(origin, path, "PUT", payload, token)
    if status != 204:
        raise RuntimeError("keycloak_bootstrap_user_profile_failed")


def _admin_token(origin: str, username: str, password: str) -> str:
    body = urllib.parse.urlencode(
        {"client_id": "admin-cli", "grant_type": "password", "username": username, "password": password}
    ).encode()
    status, payload, _ = _request(origin, "/realms/master/protocol/openid-connect/token", "POST", body, None)
    value = _json_object(status, payload, 200, "keycloak_bootstrap_admin_login_failed")
    token = value.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("keycloak_bootstrap_admin_token_invalid")
    return token


def _ensure_user(origin: str, token: str, username: str) -> tuple[str, bool]:
    user_id = _find_user(origin, token, username)
    if user_id is not None:
        return user_id, False
    payload = json.dumps({"username": username, "enabled": True}, separators=(",", ":")).encode()
    status, _, _ = _request(origin, f"/admin/realms/{_REALM}/users", "POST", payload, token)
    if status != 201:
        raise RuntimeError("keycloak_bootstrap_user_create_failed")
    user_id = _find_user(origin, token, username)
    if user_id is None:
        raise RuntimeError("keycloak_bootstrap_created_user_missing")
    return user_id, True


def _find_user(origin: str, token: str, username: str) -> str | None:
    query = urllib.parse.urlencode({"username": username, "exact": "true"})
    status, payload, _ = _request(origin, f"/admin/realms/{_REALM}/users?{query}", "GET", None, token)
    values = _json_list(status, payload, 200, "keycloak_bootstrap_user_lookup_failed")
    matches = [item.get("id") for item in values if isinstance(item, dict) and item.get("username") == username]
    if len(matches) > 1 or (matches and (not isinstance(matches[0], str) or not matches[0])):
        raise RuntimeError("keycloak_bootstrap_user_lookup_ambiguous")
    return matches[0] if matches else None


def _reset_password(origin: str, token: str, user_id: str, password: str) -> None:
    payload = json.dumps({"type": "password", "value": password, "temporary": False}, separators=(",", ":")).encode()
    status, _, _ = _request(
        origin,
        f"/admin/realms/{_REALM}/users/{urllib.parse.quote(user_id, safe='')}/reset-password",
        "PUT",
        payload,
        token,
    )
    if status != 204:
        raise RuntimeError("keycloak_bootstrap_password_reset_failed")


def _assign_roles(origin: str, token: str, user_id: str) -> None:
    roles = [_realm_role(origin, token, role) for role in _ROLES]
    payload = json.dumps(roles, separators=(",", ":")).encode()
    path = f"/admin/realms/{_REALM}/users/{urllib.parse.quote(user_id, safe='')}/role-mappings/realm"
    status, _, _ = _request(origin, path, "POST", payload, token)
    if status != 204:
        raise RuntimeError("keycloak_bootstrap_role_assignment_failed")


def _realm_role(origin: str, token: str, role: str) -> dict[str, object]:
    path = f"/admin/realms/{_REALM}/roles/{urllib.parse.quote(role, safe='')}"
    status, payload, _ = _request(origin, path, "GET", None, token)
    value = _json_object(status, payload, 200, "keycloak_bootstrap_role_lookup_failed")
    if value.get("name") != role or not isinstance(value.get("id"), str):
        raise RuntimeError("keycloak_bootstrap_role_lookup_invalid")
    return value


def _ensure_runtime_claim_mappers(origin: str, token: str) -> None:
    scope = _client_scope(origin, token)
    scope_id = str(scope["id"])
    existing = scope.get("protocolMappers", [])
    if not isinstance(existing, list):
        raise RuntimeError("keycloak_bootstrap_runtime_scope_invalid")
    by_name = {item.get("name"): item for item in existing if isinstance(item, dict)}
    for mapper in _RUNTIME_MAPPERS:
        current = by_name.get(mapper["name"])
        _upsert_runtime_mapper(origin, token, scope_id, mapper, current)


def _client_scope(origin: str, token: str) -> dict[str, object]:
    status, payload, _ = _request(origin, f"/admin/realms/{_REALM}/client-scopes", "GET", None, token)
    scopes = _json_list(status, payload, 200, "keycloak_bootstrap_runtime_scope_lookup_failed")
    matches = [scope for scope in scopes if isinstance(scope, dict) and scope.get("name") == _RUNTIME_SCOPE]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise RuntimeError("keycloak_bootstrap_runtime_scope_invalid")
    scope_id = urllib.parse.quote(str(matches[0]["id"]), safe="")
    status, payload, _ = _request(origin, f"/admin/realms/{_REALM}/client-scopes/{scope_id}", "GET", None, token)
    return _json_object(status, payload, 200, "keycloak_bootstrap_runtime_scope_lookup_failed")


def _upsert_runtime_mapper(
    origin: str,
    token: str,
    scope_id: str,
    mapper: dict[str, object],
    current: object,
) -> None:
    base = f"/admin/realms/{_REALM}/client-scopes/{urllib.parse.quote(scope_id, safe='')}/protocol-mappers/models"
    if isinstance(current, dict) and isinstance(current.get("id"), str):
        mapper_id = str(current["id"])
        path = f"{base}/{urllib.parse.quote(mapper_id, safe='')}"
        payload = json.dumps({**mapper, "id": mapper_id}, separators=(",", ":")).encode()
        status, _, _ = _request(origin, path, "PUT", payload, token)
        expected = 204
    else:
        payload = json.dumps(mapper, separators=(",", ":")).encode()
        status, _, _ = _request(origin, base, "POST", payload, token)
        expected = 201
    if status != expected:
        raise RuntimeError("keycloak_bootstrap_runtime_mapper_failed")


def _request(
    origin: str,
    path: str,
    method: str,
    payload: bytes | None,
    token: str | None,
) -> tuple[int, bytes, str | None]:
    url = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
    if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(origin).netloc:
        raise ValueError("keycloak_bootstrap_origin_mismatch")
    headers = {"accept": "application/json"}
    if payload is not None:
        is_token_request = path.endswith("/protocol/openid-connect/token")
        headers["content-type"] = "application/x-www-form-urlencoded" if is_token_request else "application/json"
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            status = response.status
            location = response.headers.get("location")
    except urllib.error.HTTPError as exc:
        body = exc.read(_MAX_RESPONSE_BYTES + 1)
        status = exc.code
        location = exc.headers.get("location")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("keycloak_bootstrap_unavailable") from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("keycloak_bootstrap_response_too_large")
    return status, body, location


def _json_object(status: int, payload: bytes, expected: int, reason: str) -> dict[str, object]:
    value = _json_value(status, payload, expected, reason)
    if not isinstance(value, dict):
        raise RuntimeError(reason)
    return value


def _json_list(status: int, payload: bytes, expected: int, reason: str) -> list[object]:
    value = _json_value(status, payload, expected, reason)
    if not isinstance(value, list):
        raise RuntimeError(reason)
    return value


def _json_value(status: int, payload: bytes, expected: int, reason: str) -> object:
    if status != expected:
        raise RuntimeError(reason)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(reason) from exc


def _validated_origin(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if not _is_clean_origin(parsed):
        raise ValueError("keycloak_bootstrap_base_url_invalid")
    if parsed.scheme == "http" and not _is_allowed_cleartext_host(parsed.hostname or ""):
        raise ValueError("keycloak_bootstrap_cleartext_host_forbidden")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _is_clean_origin(parsed: urllib.parse.SplitResult) -> bool:
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.path in {"", "/"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _is_allowed_cleartext_host(hostname: str) -> bool:
    return hostname in {"127.0.0.1", "localhost"} or hostname.endswith("-keycloak")


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key, "").strip()
    if not value or len(value) > 512:
        raise ValueError(f"keycloak_bootstrap_environment_invalid:{key}")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, fp: object, code: int, msg: str, headers: object, url: str) -> None:
        raise RuntimeError("keycloak_bootstrap_redirect_not_allowed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    receipt = bootstrap(parser.parse_args().base_url)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
