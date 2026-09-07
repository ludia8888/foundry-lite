"""Provision an exact private-pilot login on the dedicated Mac mini only.

Credentials stay in a mode-0600 operator handoff file, never in stdout or the
receipt. Existing user passwords and the release clients are never reset.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess  # nosec B404 - fixed kubectl/helm argv on the guarded QA host.
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from scripts.operations.bootstrap_keycloak_qa_user import _admin_token, _request, _validated_origin
from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, utc_now, write_json_receipt
from scripts.operations.private_browser_keycloak import client_payload

_REALM = "/admin/realms/foundry-lite"


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("private_browser_configuration_object_invalid")
    return value


def _command_json(argv: list[str]) -> dict[str, object]:
    result = subprocess.run(argv, check=False, capture_output=True, timeout=60)  # nosec B603 - fixed operator argv.
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise RuntimeError("private_browser_operator_command_failed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("private_browser_operator_response_invalid")
    return value


class KeycloakAdmin:
    def __init__(self, origin: str, username: str, password: str) -> None:
        self.origin = _validated_origin(origin)
        self.token = _admin_token(self.origin, username, password)

    def request(self, path: str, method: str = "GET", payload: object = None, expected: int = 200) -> object:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        status, result, _ = _request(self.origin, _REALM + path, method, body, self.token)
        if status != expected:
            raise RuntimeError(f"private_browser_identity_operation_failed:{method}:{status}")
        return json.loads(result) if result else None


def _ensure_redirect_host(admin: KeycloakAdmin, public_base: str) -> None:
    payload = admin.request("/client-policies/profiles")
    if not isinstance(payload, dict) or not isinstance(payload.get("profiles"), list):
        raise RuntimeError("private_browser_redirect_policy_missing")
    profiles = [item for item in payload["profiles"] if item.get("name") == "foundry-lite-public-oauth"]
    if len(profiles) != 1:
        raise RuntimeError("private_browser_redirect_policy_ambiguous")
    executors = [item for item in profiles[0]["executors"] if item["executor"] == "secure-redirect-uris-enforcer"]
    if len(executors) != 1:
        raise RuntimeError("private_browser_redirect_enforcer_missing")
    domains = executors[0]["configuration"]["allow-permitted-domains"]
    domain = re.escape(str(urlsplit(public_base).hostname))
    if domain not in domains:
        domains.append(domain)
        admin.request("/client-policies/profiles", "PUT", payload, 204)


def _write_once_or_match(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise RuntimeError("private_browser_receipt_not_private")
        existing = json.loads(path.read_text())
        # Replays retain the original creation time and never overwrite a
        # mismatching owner's settings or credentials.
        if {k: v for k, v in existing.items() if k != "createdAt"} != {
            k: v for k, v in payload.items() if k != "createdAt"
        }:
            raise RuntimeError("private_browser_receipt_replay_mismatch")
        return
    write_json_receipt(path, payload)


def _ensure_client(admin: KeycloakAdmin, desired: dict[str, object]) -> None:
    rows = admin.request("/clients?" + urlencode({"clientId": desired["clientId"]}))
    if not isinstance(rows, list) or len(rows) > 1:
        raise RuntimeError("private_browser_client_lookup_ambiguous")
    if not rows:
        admin.request("/clients", "POST", desired, 201)
        return
    existing = rows[0]
    marker = existing.get("attributes", {}).get("foundry.private.application")
    if marker != _object(desired["attributes"])["foundry.private.application"]:
        raise RuntimeError("private_browser_existing_client_not_owned")
    admin.request(f"/clients/{quote(existing['id'], safe='')}", "PUT", {**desired, "id": existing["id"]}, 204)


def _initial_password(email: str, application_id: str, state_dir: Path, public_base: str) -> str:
    credential_path = state_dir / "first-login.json"
    if credential_path.exists():
        if credential_path.is_symlink() or credential_path.stat().st_mode & 0o077:
            raise RuntimeError("private_browser_credential_file_not_private")
        handoff = json.loads(credential_path.read_text())
        if handoff["email"] != email or handoff["applicationId"] != application_id:
            raise RuntimeError("private_browser_credential_owner_mismatch")
        password = handoff["initialPassword"]
    else:
        password = secrets.token_urlsafe(32)
        handoff = {
            "email": email,
            "applicationId": application_id,
            "initialPassword": password,
            "loginUrl": f"{public_base}/apps/{application_id}",
            "mustChangePassword": True,
        }
        descriptor = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(handoff, output, ensure_ascii=False)
    return str(password)


def _verify_private_client(admin: KeycloakAdmin, client_id: str) -> None:
    rows = admin.request("/clients?" + urlencode({"clientId": client_id}))
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("private_browser_client_verification_failed")
    client = rows[0]
    scopes = admin.request(f"/clients/{quote(client['id'], safe='')}/default-client-scopes")
    if not isinstance(scopes, list) or {item["name"] for item in scopes} != {"profile", "email"}:
        raise RuntimeError("private_browser_client_inherits_unexpected_scope")
    if client.get("attributes", {}).get("pkce.code.challenge.method") != "S256":
        raise RuntimeError("private_browser_pkce_not_enforced")
    if any(
        client.get(name)
        for name in ("implicitFlowEnabled", "directAccessGrantsEnabled", "serviceAccountsEnabled", "fullScopeAllowed")
    ):
        raise RuntimeError("private_browser_client_has_unexpected_grant")


def _ensure_user(admin: KeycloakAdmin, email: str, application_id: str, state_dir: Path, public_base: str) -> str:
    rows = admin.request("/users?" + urlencode({"username": email, "exact": "true"}))
    if not isinstance(rows, list) or len(rows) > 1:
        raise RuntimeError("private_browser_owner_lookup_ambiguous")
    if rows:
        if rows[0].get("attributes", {}).get("foundry_private_app") != [application_id]:
            raise RuntimeError("private_browser_existing_owner_requires_review")
        return str(rows[0]["id"])
    password = _initial_password(email, application_id, state_dir, public_base)
    admin.request(
        "/users",
        "POST",
        {
            "username": email,
            "email": email,
            "emailVerified": False,
            "enabled": True,
            "firstName": "업무",
            "lastName": "사용자",
            "requiredActions": ["UPDATE_PASSWORD"],
            "attributes": {"foundry_private_app": [application_id]},
            "credentials": [{"type": "password", "value": password, "temporary": True}],
        },
        201,
    )
    rows = admin.request("/users?" + urlencode({"username": email, "exact": "true"}))
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("private_browser_created_owner_missing")
    return str(rows[0]["id"])


def bootstrap(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.application_id) is None:
        raise ValueError("private_browser_application_id_invalid")
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.email) is None:
        raise ValueError("private_browser_email_invalid")
    state_dir = QA_ROOT / "state" / f"private-browser-{args.application_id}"
    if state_dir.is_symlink():
        raise RuntimeError("private_browser_state_symlink_forbidden")
    state_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(state_dir, 0o700)
    kubeconfig = str(QA_ROOT / "state" / "kubeconfig")
    values = _command_json(
        [
            str(QA_ROOT / "bin" / "helm"),
            "get",
            "values",
            "foundry-lite",
            "-n",
            "foundry-qa",
            "--kubeconfig",
            kubeconfig,
            "--all",
            "-o",
            "json",
        ]
    )
    qa_dependencies = _object(values["qaDependencies"])
    oidc = _object(_object(values["external"])["oidc"])
    identity = str(_object(qa_dependencies["keycloak"])["publicBaseUrl"])
    public_base = str(_object(values["mcp"])["publicBaseUrl"])
    audience = str(oidc["audience"])
    desired = client_payload(args.client_id, args.application_id, args.tenant_id, public_base, audience)
    secret = _command_json(
        [
            str(QA_ROOT / "bin" / "kubectl"),
            "--kubeconfig",
            kubeconfig,
            "-n",
            "foundry-qa",
            "get",
            "secret",
            str(qa_dependencies["credentialsExistingSecret"]),
            "-o",
            "json",
        ]
    )
    data = _object(secret["data"])
    admin = KeycloakAdmin(
        identity,
        base64.b64decode(str(data["KEYCLOAK_ADMIN"])).decode(),
        base64.b64decode(str(data["KEYCLOAK_ADMIN_PASSWORD"])).decode(),
    )
    realm = admin.request("")
    if not isinstance(realm, dict) or realm.get("registrationAllowed") is not False:
        raise RuntimeError("private_browser_public_signup_must_be_disabled")
    _ensure_redirect_host(admin, public_base)
    _ensure_client(admin, desired)
    _verify_private_client(admin, args.client_id)
    subject = _ensure_user(admin, args.email, args.application_id, state_dir, public_base)
    clients = sorted(set(json.loads(str(oidc["allowedClientIdsJson"]))) | {args.client_id})
    override: dict[str, object] = {
        "browserAuth": {
            "clientId": args.client_id,
            "publicBaseUrl": public_base,
            "applicationId": args.application_id,
            "ownerSubject": subject,
        },
        "external": {"oidc": {"allowedClientIdsJson": json.dumps(clients, separators=(",", ":"))}},
    }
    _write_once_or_match(state_dir / "helm-values.json", override)
    receipt = {
        "status": "identity_configured",
        "applicationId": args.application_id,
        "subject": subject,
        "clientId": args.client_id,
        "roles": ["viewer"],
        "publicSignupEnabled": False,
        "passwordResetOnReplay": False,
        "passwordStoredInReceipt": False,
        "createdAt": utc_now(),
        "credentialHandoffPath": str(state_dir / "first-login.json"),
        "helmValuesPath": str(state_dir / "helm-values.json"),
        "deploymentApplied": False,
    }
    _write_once_or_match(state_dir / "identity-receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--email", required=True)
    print(json.dumps(bootstrap(parser.parse_args()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
