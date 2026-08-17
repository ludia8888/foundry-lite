"""Atomically switch the dedicated Mac mini QA release to production OIDC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404 - fixed Helm/Kubectl argv under the Mac mini guard.
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    utc_now,
    write_json_receipt,
)

_RELEASE = "foundry-lite"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_APPLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_HELM_OUTPUT = 8 * 1024 * 1024


def switch(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    if _RUN_ID.fullmatch(args.run_id) is None or _APPLICATION_ID.fullmatch(args.application_id) is None:
        raise ValueError("macmini_oidc_switch_identifier_invalid")
    chart = _qa_path(args.chart, is_directory=True)
    kubeconfig = _qa_path(args.kubeconfig, is_directory=False)
    public_base = _clean_https_origin(args.public_base_url, "macmini_oidc_switch_public_base_invalid")
    identity_base = _clean_https_origin(args.identity_base_url, "macmini_oidc_switch_identity_base_invalid")
    clients = _allowed_clients(args.allowed_client_id)
    desired = _desired_values(public_base, identity_base, args.application_id, clients)
    before = _helm_values(args)
    if _is_exact_oidc(before, desired):
        phase = "already_configured"
    else:
        _require_embedded_oauth(before)
        override = QA_ROOT / "state" / f"{args.run_id}-external-oidc.json"
        _write_or_validate_private_json(override, desired)
        _helm_upgrade(args, chart, kubeconfig, override)
        phase = "upgraded"
    after = _helm_values(args)
    if not _is_exact_oidc(after, desired):
        raise RuntimeError("macmini_oidc_switch_reconciliation_failed")
    _require_rollouts(args, kubeconfig)
    receipt = _receipt(args, desired, before, after, phase)
    target = QA_ROOT / "evidence" / args.run_id / "external-oidc-switch.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _desired_values(
    public_base: str,
    identity_base: str,
    application_id: str,
    clients: frozenset[str],
) -> dict[str, object]:
    issuer = f"{identity_base}/realms/foundry-lite"
    audience = f"{public_base}/mcp/release/{application_id}"
    return {
        "global": {"runtimeProfile": "production"},
        "secrets": {"applicationExistingSecret": "foundry-lite-runtime-application"},
        "auth": {
            "profile": "oidc",
            "localOAuthIssuer": "",
            "dynamicClientApplicationId": "",
            "localConsentRoles": "",
        },
        "mcp": {
            "publicBaseUrl": public_base,
            "authorizationServer": issuer,
            "governedReleaseApplicationId": application_id,
            "resourceAudienceScopePrefix": "mcp-audience",
        },
        "qaDependencies": {"keycloak": {"publicBaseUrl": identity_base}},
        "external": {
            "oidc": {
                "issuer": issuer,
                "discoveryUrl": f"{issuer}/.well-known/openid-configuration",
                "audience": audience,
                "allowedClientIdsJson": json.dumps(sorted(clients), separators=(",", ":")),
                "clientIdClaim": "azp",
                "sessionClaim": "sid",
                "humanGrantClaim": "human_grant",
                "humanGrantValue": "true",
                "grantTypeClaim": "authorization_grant_type",
                "grantTypeValue": "authorization_code",
                "jwksRefreshIntervalSeconds": 60,
                "retiredKeyGraceSeconds": 300,
            }
        },
    }


def _helm_values(args: argparse.Namespace) -> dict[str, object]:
    kubeconfig = _qa_path(args.kubeconfig, is_directory=False)
    result = subprocess.run(  # nosec B603 - fixed Helm argv and guarded namespace.
        (
            args.helm,
            "get",
            "values",
            _RELEASE,
            "--namespace",
            args.namespace,
            "--kubeconfig",
            str(kubeconfig),
            "--all",
            "--output",
            "json",
        ),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0 or len(result.stdout) > _MAX_HELM_OUTPUT:
        raise RuntimeError("macmini_oidc_switch_helm_values_failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_oidc_switch_helm_values_failed") from exc
    if not isinstance(value, dict):
        raise RuntimeError("macmini_oidc_switch_helm_values_failed")
    return value


def _require_embedded_oauth(values: Mapping[str, object]) -> None:
    global_values = _mapping(values, "global")
    auth = _mapping(values, "auth")
    oidc = _mapping(_mapping(values, "external"), "oidc")
    expected = (
        global_values.get("runtimeProfile") == "test",
        auth.get("profile") == "header-trust",
        oidc.get("discoveryUrl") == "",
    )
    if not all(expected):
        raise RuntimeError("macmini_oidc_switch_source_profile_unexpected")


def _is_exact_oidc(values: Mapping[str, object], desired: Mapping[str, object]) -> bool:
    try:
        return all(_contains(values, key, value) for key, value in desired.items())
    except (TypeError, ValueError):
        return False


def _contains(actual: Mapping[str, object], key: str, expected: object) -> bool:
    value = actual.get(key)
    if isinstance(expected, Mapping):
        return isinstance(value, Mapping) and all(_contains(value, child, item) for child, item in expected.items())
    return value == expected


def _helm_upgrade(args: argparse.Namespace, chart: Path, kubeconfig: Path, override: Path) -> None:
    result = subprocess.run(  # nosec B603 - fixed Helm argv and guarded paths.
        (
            args.helm,
            "upgrade",
            _RELEASE,
            str(chart),
            "--namespace",
            args.namespace,
            "--kubeconfig",
            str(kubeconfig),
            "--reuse-values",
            "--values",
            str(override),
            "--atomic",
            "--wait",
            "--wait-for-jobs",
            "--timeout",
            "20m",
        ),
        check=False,
        capture_output=True,
        timeout=1300,
    )
    if result.returncode != 0:
        raise RuntimeError("macmini_oidc_switch_helm_upgrade_failed")


def _require_rollouts(args: argparse.Namespace, kubeconfig: Path) -> None:
    targets = (("statefulset", "foundry-lite-keycloak"), ("deployment", "foundry-lite"))
    for kind, name in targets:
        result = subprocess.run(  # nosec B603 - fixed kubectl rollout argv.
            (
                args.kubectl,
                "--kubeconfig",
                str(kubeconfig),
                "--namespace",
                args.namespace,
                "rollout",
                "status",
                f"{kind}/{name}",
                "--timeout=5m",
            ),
            check=False,
            capture_output=True,
            timeout=330,
        )
        if result.returncode != 0:
            raise RuntimeError("macmini_oidc_switch_rollout_failed")


def _receipt(
    args: argparse.Namespace,
    desired: Mapping[str, object],
    before: Mapping[str, object],
    after: Mapping[str, object],
    phase: str,
) -> dict[str, object]:
    oidc = _mapping(_mapping(desired, "external"), "oidc")
    return {
        "schemaVersion": 1,
        "status": "passed",
        "phase": phase,
        "runId": args.run_id,
        "recordedAt": utc_now(),
        "namespace": args.namespace,
        "authProfile": "oidc",
        "runtimeProfile": "production",
        "issuer": oidc["issuer"],
        "audience": oidc["audience"],
        "discoveryUrl": oidc["discoveryUrl"],
        "allowedClientIdsSha256": _hash(str(oidc["allowedClientIdsJson"])),
        "beforeValuesSha256": _hash_json(before),
        "afterValuesSha256": _hash_json(after),
        "rawTokensStored": False,
        "rawSecretsStored": False,
        "otherNamespacesMutated": False,
    }


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"macmini_oidc_switch_values_invalid:{key}")
    return item


def _allowed_clients(values: Sequence[str]) -> frozenset[str]:
    clients = frozenset(value.strip() for value in values if value.strip())
    if not clients or any(len(value) > 255 for value in clients):
        raise ValueError("macmini_oidc_switch_clients_invalid")
    return clients


def _clean_https_origin(value: str, reason: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(reason)
    return value.rstrip("/")


def _qa_path(raw: str, *, is_directory: bool) -> Path:
    path = Path(raw)
    if path.is_symlink():
        raise ValueError("macmini_oidc_switch_path_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("macmini_oidc_switch_path_invalid") from exc
    if QA_ROOT not in resolved.parents:
        raise ValueError("macmini_oidc_switch_path_invalid")
    if (is_directory and not resolved.is_dir()) or (not is_directory and not resolved.is_file()):
        raise ValueError("macmini_oidc_switch_path_invalid")
    return resolved


def _write_or_validate_private_json(path: Path, value: object) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or path.read_bytes() != encoded:
            raise RuntimeError("macmini_oidc_switch_override_conflict") from None
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _hash_json(value: Mapping[str, object]) -> str:
    return _hash(json.dumps(value, separators=(",", ":"), sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--helm", default=str(QA_ROOT / "bin" / "helm"))
    parser.add_argument("--chart", required=True)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--identity-base-url", required=True)
    parser.add_argument("--application-id", default="foundry-lite")
    parser.add_argument("--allowed-client-id", action="append", required=True)
    switch(parser.parse_args(argv))
    print('{"receiptStored": true, "status": "passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
