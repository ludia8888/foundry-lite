"""Collect live PostgreSQL object-store evidence from the protected API Pod."""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 - fixed kubectl argv under the Mac mini guard.
import time
from pathlib import Path

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    utc_now,
    write_json_receipt,
)

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,100}$")
_API_IMAGE = re.compile(r"^ghcr\.io/ludia8888/foundry-lite-api@sha256:[0-9a-f]{64}$")
_RUNTIME_ENV_FROM_NAME = "foundry-lite-runtime-application"


def collect(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    if _RUN_ID.fullmatch(args.run_id) is None or _TENANT_ID.fullmatch(args.tenant_id) is None:
        raise ValueError("macmini_postgres_object_store_identifier_invalid")
    kubeconfig = _qa_path(args.kubeconfig)
    image = _deployed_api_image(args, kubeconfig)
    pod_name = _pod_name(args.run_id)
    result = _run_verifier_pod(args, kubeconfig, pod_name, image)
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("macmini_postgres_object_store_runtime_check_failed")
    evidence = _validated_evidence(_last_json_object(result.stdout))
    receipt = {
        **evidence,
        "runId": args.run_id,
        "namespace": args.namespace,
        "verifiedAt": utc_now(),
        "verifierPodDeleted": True,
        "otherNamespacesMutated": False,
    }
    target = QA_ROOT / "evidence" / args.run_id / "postgres-object-store-live.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _deployed_api_image(args: argparse.Namespace, kubeconfig: Path) -> str:
    result = _kubectl(args, kubeconfig, ("get", "deployment", "foundry-lite", "-o", "json"), 60)
    if result.returncode != 0 or len(result.stdout) > 4 * 1024 * 1024:
        raise RuntimeError("macmini_postgres_object_store_image_lookup_failed")
    try:
        deployment = json.loads(result.stdout)
        containers = deployment["spec"]["template"]["spec"]["containers"]
        image = next(item["image"] for item in containers if item["name"] == "api")
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise RuntimeError("macmini_postgres_object_store_image_lookup_failed") from exc
    if not isinstance(image, str) or _API_IMAGE.fullmatch(image) is None:
        raise RuntimeError("macmini_postgres_object_store_image_lookup_failed")
    return image


def _run_verifier_pod(
    args: argparse.Namespace,
    kubeconfig: Path,
    pod_name: str,
    image: str,
) -> subprocess.CompletedProcess[bytes]:
    created = False
    primary_error: BaseException | None = None
    try:
        manifest = json.dumps(
            _pod_manifest(pod_name, image, args.tenant_id),
            separators=(",", ":"),
        ).encode()
        created_result = _kubectl(args, kubeconfig, ("create", "-f", "-"), 60, input_bytes=manifest)
        if created_result.returncode != 0:
            raise RuntimeError("macmini_postgres_object_store_verifier_create_failed")
        created = True
        _wait_for_completion(args, kubeconfig, pod_name)
        return _kubectl(args, kubeconfig, ("logs", f"pod/{pod_name}", "-c", "verifier"), 60)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if created:
            deleted = _kubectl(
                args,
                kubeconfig,
                ("delete", f"pod/{pod_name}", "--ignore-not-found=true", "--wait=true", "--timeout=60s"),
                90,
            )
            if deleted.returncode != 0 and primary_error is None:
                raise RuntimeError("macmini_postgres_object_store_verifier_cleanup_failed")


def _pod_manifest(pod_name: str, image: str, tenant_id: str) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {"app.kubernetes.io/component": "postgres-object-store-verifier"},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": "foundry-lite-ghcr"}],
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001},
            "containers": [_verifier_container(image, tenant_id)],
        },
    }


def _verifier_container(image: str, tenant_id: str) -> dict[str, object]:
    return {
        "name": "verifier",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": [
            "/opt/foundry-lite-venv/bin/python",
            "/app/scripts/operations/verify_postgres_object_store_runtime.py",
            "--tenant-id",
            tenant_id,
            "--expected-role",
            "foundry_lite_app",
        ],
        "envFrom": [{"secretRef": {"name": _RUNTIME_ENV_FROM_NAME}}],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "resources": {
            "requests": {"cpu": "25m", "memory": "64Mi"},
            "limits": {"cpu": "250m", "memory": "256Mi"},
        },
    }


def _pod_name(run_id: str) -> str:
    suffix = re.sub(r"[^a-z0-9-]", "-", run_id.lower().replace("_", "-")).strip("-")
    return f"foundry-lite-postgres-object-store-{suffix[:25].rstrip('-')}"


def _wait_for_completion(args: argparse.Namespace, kubeconfig: Path, pod_name: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = _kubectl(args, kubeconfig, ("get", f"pod/{pod_name}", "-o", "json"), 30)
        if result.returncode == 0:
            try:
                phase = json.loads(result.stdout).get("status", {}).get("phase")
            except (json.JSONDecodeError, AttributeError):
                phase = None
            if phase == "Succeeded":
                return
            if phase == "Failed":
                raise RuntimeError("macmini_postgres_object_store_verifier_failed")
        time.sleep(2)
    raise RuntimeError("macmini_postgres_object_store_verifier_timeout")


def _kubectl(
    args: argparse.Namespace,
    kubeconfig: Path,
    operation: tuple[str, ...],
    timeout: int,
    *,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = (
        args.kubectl,
        "--kubeconfig",
        str(kubeconfig),
        "--namespace",
        args.namespace,
        *operation,
    )
    return subprocess.run(  # nosec B603 - fixed namespace-bound kubectl operations only.
        command,
        input=input_bytes,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def _validated_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("macmini_postgres_object_store_evidence_invalid")
    expected = {
        "status": "passed",
        "databaseBackend": "postgresql",
        "runtimeRole": "foundry_lite_app",
        "isSuperuser": False,
        "canBypassRls": False,
        "jsonbColumnCount": 15,
        "productionIndexCount": 10,
        "jsonbPathOpsGinIndexCount": 2,
        "forcedRlsTableCount": 9,
        "noTenantRowsVisible": True,
        "otherTenantRowsVisible": False,
        "crossTenantWriteBlocked": True,
        "rawCredentialsStored": False,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise RuntimeError("macmini_postgres_object_store_evidence_invalid")
    if not isinstance(value.get("visibleTenantRowCount"), int) or value["visibleTenantRowCount"] < 1:
        raise RuntimeError("macmini_postgres_object_store_evidence_invalid")
    return value


def _last_json_object(value: bytes) -> dict[str, object] | None:
    for raw_line in reversed(value.splitlines()):
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _qa_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_symlink():
        raise ValueError("macmini_postgres_object_store_kubeconfig_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("macmini_postgres_object_store_kubeconfig_invalid") from exc
    if resolved.parent != QA_ROOT / "state" or not resolved.is_file():
        raise ValueError("macmini_postgres_object_store_kubeconfig_invalid")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--tenant-id", default="tenant-demo")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    collect(parser.parse_args(argv))
    print('{"receiptStored":true,"status":"passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
