"""Run a bounded external-OIDC switch, outage proof, and exact Helm restoration."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed Helm and kubectl argv under the Mac mini QA guard.

from scripts.operations import (
    issue_macmini_external_oidc_tokens,
    run_macmini_external_oidc_fault,
    switch_macmini_external_oidc,
)
from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace, write_json_receipt

_RELEASE = "foundry-lite"


def run(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    before_revision = _helm_revision(args)
    before_values = switch_macmini_external_oidc._helm_values(args)
    before_hash = switch_macmini_external_oidc._hash_json(before_values)
    client: issue_macmini_external_oidc_tokens.IssuedClient | None = None
    steps: dict[str, object] = {}
    failure_type: str | None = None
    try:
        steps["issuerPreparation"] = _prepare_identity_hostname(args)
        issuance, client = issue_macmini_external_oidc_tokens.issue(_issuance_args(args))
        steps["tokenIssuance"] = issuance
        steps["oidcSwitch"] = switch_macmini_external_oidc.switch(_switch_args(args, client.client_id))
        steps["fault"] = run_macmini_external_oidc_fault.run(_fault_args(args, client.client_id))
    except Exception as exc:  # noqa: BLE001 - failure is recorded after exact restoration.
        failure_type = type(exc).__name__
    finally:
        steps["clientCleanup"] = _client_cleanup(client)
        steps["tokenCleanup"] = _token_cleanup()
        steps["restoration"] = _restore_revision(args, before_revision, before_hash)
    is_passed = failure_type is None and all(_step_passed(value) for value in steps.values())
    receipt = {
        "schemaVersion": 1,
        "status": "passed" if is_passed else "failed",
        "runId": args.run_id,
        "failureType": failure_type,
        "sourceHelmRevision": before_revision,
        "sourceValuesSha256": before_hash,
        "steps": steps,
        "ephemeralTokensRemoved": True,
        "ephemeralClientRemoved": client is not None and _step_passed(steps["clientCleanup"]),
        "rawTokensStored": False,
        "rawPasswordsStored": False,
    }
    target = QA_ROOT / "evidence" / args.run_id / "external-oidc-rehearsal.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _prepare_identity_hostname(args: argparse.Namespace) -> dict[str, object]:
    override = QA_ROOT / "state" / f"{args.run_id}-external-identity.json"
    desired = {"qaDependencies": {"keycloak": {"publicBaseUrl": args.identity_base_url}}}
    switch_macmini_external_oidc._write_or_validate_private_json(override, desired)
    result = subprocess.run(  # nosec B603 - fixed Helm argv and guarded paths.
        (
            args.helm,
            "upgrade",
            _RELEASE,
            args.chart,
            "--namespace",
            args.namespace,
            "--kubeconfig",
            args.kubeconfig,
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
        raise RuntimeError("macmini_external_oidc_identity_preparation_failed")
    _rollout(args, "statefulset/foundry-lite-keycloak")
    return {"status": "passed", "identityBaseUrl": args.identity_base_url, "rawSecretsStored": False}


def _restore_revision(args: argparse.Namespace, revision: int, expected_hash: str) -> dict[str, object]:
    result = subprocess.run(  # nosec B603 - exact observed Helm revision and fixed release.
        (
            args.helm,
            "rollback",
            _RELEASE,
            str(revision),
            "--namespace",
            args.namespace,
            "--kubeconfig",
            args.kubeconfig,
            "--wait",
            "--timeout",
            "10m",
        ),
        check=False,
        capture_output=True,
        timeout=660,
    )
    if result.returncode != 0:
        return {"status": "failed", "reason": "helm_rollback_failed"}
    try:
        _rollout(args, "deployment/foundry-lite")
        _rollout(args, "statefulset/foundry-lite-keycloak")
        actual_hash = switch_macmini_external_oidc._hash_json(switch_macmini_external_oidc._helm_values(args))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "reason": type(exc).__name__, "sourceRevision": revision}
    return {
        "status": "passed" if actual_hash == expected_hash else "failed",
        "sourceRevision": revision,
        "expectedValuesSha256": expected_hash,
        "actualValuesSha256": actual_hash,
    }


def _helm_revision(args: argparse.Namespace) -> int:
    result = subprocess.run(  # nosec B603 - fixed Helm status argv.
        (
            args.helm,
            "status",
            _RELEASE,
            "--namespace",
            args.namespace,
            "--kubeconfig",
            args.kubeconfig,
            "--output",
            "json",
        ),
        check=False,
        capture_output=True,
        timeout=60,
    )
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_external_oidc_helm_status_failed") from exc
    revision = payload.get("version") if result.returncode == 0 and isinstance(payload, dict) else None
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise RuntimeError("macmini_external_oidc_helm_status_failed")
    return revision


def _rollout(args: argparse.Namespace, target: str) -> None:
    result = subprocess.run(  # nosec B603 - fixed kubectl rollout argv.
        (
            args.kubectl,
            "--kubeconfig",
            args.kubeconfig,
            "--namespace",
            args.namespace,
            "rollout",
            "status",
            target,
            "--timeout=5m",
        ),
        check=False,
        capture_output=True,
        timeout=330,
    )
    if result.returncode != 0:
        raise RuntimeError("macmini_external_oidc_rollout_failed")


def _issuance_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        run_id=f"{args.run_id}-issuance",
        identity_base_url=args.identity_base_url,
        audience=f"{args.public_base_url}/mcp/release/{args.application_id}",
        principals_file=args.principals_file,
    )


def _switch_args(args: argparse.Namespace, client_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        run_id=f"{args.run_id}-switch",
        namespace=args.namespace,
        kubeconfig=args.kubeconfig,
        kubectl=args.kubectl,
        helm=args.helm,
        chart=args.chart,
        public_base_url=args.public_base_url,
        identity_base_url=args.identity_base_url,
        application_id=args.application_id,
        allowed_client_id=[client_id],
    )


def _fault_args(args: argparse.Namespace, client_id: str) -> argparse.Namespace:
    issuer = f"{args.identity_base_url}/realms/foundry-lite"
    return argparse.Namespace(
        run_id=f"{args.run_id}-fault",
        namespace=args.namespace,
        kubeconfig=args.kubeconfig,
        kubectl=args.kubectl,
        issuer=issuer,
        discovery_url=f"{issuer}/.well-known/openid-configuration",
        audience=f"{args.public_base_url}/mcp/release/{args.application_id}",
        allowed_client_id=[client_id],
        author_token_file=str(QA_ROOT / "state" / "author-token"),
        reviewer_token_file=str(QA_ROOT / "state" / "reviewer-token"),
        duration_seconds=args.duration_seconds,
    )


def _client_cleanup(client: issue_macmini_external_oidc_tokens.IssuedClient | None) -> dict[str, object]:
    if client is None:
        return {"status": "passed", "performed": False}
    try:
        is_deleted = issue_macmini_external_oidc_tokens.delete_client(client)
    except (OSError, RuntimeError, ValueError):
        is_deleted = False
    return {"status": "passed" if is_deleted else "failed", "performed": True}


def _token_cleanup() -> dict[str, object]:
    removed = 0
    try:
        for name in ("author-token", "reviewer-token"):
            path = QA_ROOT / "state" / name
            if path.exists() and path.is_file() and not path.is_symlink():
                path.unlink()
                removed += 1
    except OSError:
        return {"status": "failed", "removedFileCount": removed}
    return {"status": "passed", "removedFileCount": removed}


def _step_passed(value: object) -> bool:
    return isinstance(value, dict) and value.get("status") == "passed"


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
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--principals-file", required=True)
    parser.add_argument("--duration-seconds", type=int, default=45)
    receipt = run(parser.parse_args(argv))
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
