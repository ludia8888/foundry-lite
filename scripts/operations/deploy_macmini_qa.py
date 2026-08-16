"""Install the immutable ARM64 release into sean1234's dedicated k3s namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - fixed Helm/kubectl only; remove if arbitrary command input is introduced.
from pathlib import Path
from typing import cast

import yaml

from scripts.operations.bootstrap_macmini_qa_secrets import bootstrap as bootstrap_secrets
from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    ensure_qa_directories,
    utc_now,
    write_json_receipt,
)

_RELEASE = "foundry-lite"
_IMAGE_NAMES = ("api", "web", "controller", "codeExecution", "nodeCodeExecution", "trainedModel")
_IMAGE_REPOSITORIES = {
    "api": "ghcr.io/ludia8888/foundry-lite-api",
    "web": "ghcr.io/ludia8888/foundry-lite-web",
    "controller": "ghcr.io/ludia8888/foundry-lite-controller",
    "codeExecution": "ghcr.io/ludia8888/foundry-lite-code-execution",
    "nodeCodeExecution": "ghcr.io/ludia8888/foundry-lite-node-code-execution",
    "trainedModel": "ghcr.io/ludia8888/foundry-lite-trained-model",
}
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def deploy(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    if _RUN_ID.fullmatch(args.run_id) is None:
        raise ValueError("macmini_qa_run_id_invalid")
    ensure_qa_directories()
    chart = _qa_input_path(args.chart, is_directory=True)
    values = _qa_input_path(args.values, is_directory=False)
    initial_auth_values = _qa_input_path(args.initial_auth_values, is_directory=False)
    _validate_initial_auth_values(initial_auth_values)
    manifest = _load_manifest(_qa_input_path(args.image_manifest, is_directory=False))
    _ensure_namespace(args)
    _assert_fresh_release(args)
    secret_receipt = bootstrap_secrets(args)
    override, foundation = _write_overrides(args.run_id, manifest)
    value_files = (values, initial_auth_values)
    foundation_result = _helm(args, chart, value_files, override, foundation)
    final_result = _helm(args, chart, value_files, override, None)
    evidence = _collect_evidence(args)
    receipt = _receipt(args, manifest, value_files, override, secret_receipt, foundation_result, final_result, evidence)
    target = QA_ROOT / "evidence" / args.run_id / "deployment-receipt.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def _load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not _REVISION.fullmatch(str(value.get("revision", ""))):
        raise ValueError("macmini_qa_image_manifest_revision_invalid")
    raw_images = value.get("images")
    if not isinstance(raw_images, dict) or set(raw_images) != set(_IMAGE_NAMES):
        raise ValueError("macmini_qa_image_manifest_images_invalid")
    images = {name: _image_coordinates(name, raw_images[name]) for name in _IMAGE_NAMES}
    return {"revision": value["revision"], "images": images}


def _image_coordinates(name: str, value: object) -> dict[str, str]:
    if not isinstance(value, str) or value.count("@") != 1:
        raise ValueError("macmini_qa_image_coordinate_invalid")
    repository, digest = value.split("@", 1)
    if repository != _IMAGE_REPOSITORIES[name] or not _DIGEST.fullmatch(digest):
        raise ValueError("macmini_qa_image_coordinate_invalid")
    return {"repository": repository, "digest": digest}


def _validate_initial_auth_values(path: Path) -> None:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("macmini_qa_initial_auth_values_invalid")
    global_values = value.get("global")
    auth = value.get("auth")
    mcp = value.get("mcp")
    oidc = value.get("external", {}).get("oidc") if isinstance(value.get("external"), dict) else None
    if not all(isinstance(item, dict) for item in (global_values, auth, mcp, oidc)):
        raise ValueError("macmini_qa_initial_auth_values_invalid")
    global_mapping = cast(dict[str, object], global_values)
    auth_mapping = cast(dict[str, object], auth)
    mcp_mapping = cast(dict[str, object], mcp)
    oidc_mapping = cast(dict[str, object], oidc)
    expected = (
        global_mapping.get("runtimeProfile") == "test",
        auth_mapping.get("profile") == "header-trust",
        isinstance(auth_mapping.get("localOAuthIssuer"), str),
        auth_mapping.get("localOAuthIssuer") == mcp_mapping.get("publicBaseUrl"),
        bool(auth_mapping.get("dynamicClientApplicationId")),
        bool(auth_mapping.get("localConsentRoles")),
        oidc_mapping.get("discoveryUrl") == "",
    )
    if not all(expected):
        raise ValueError("macmini_qa_initial_auth_values_invalid")


def _write_overrides(run_id: str, manifest: dict[str, object]) -> tuple[Path, Path]:
    state = QA_ROOT / "state"
    override = state / f"{run_id}-immutable-images.json"
    foundation = state / f"{run_id}-foundation.json"
    _write_private_json(override, {"global": {"revision": manifest["revision"]}, "images": manifest["images"]})
    _write_private_json(
        foundation,
        {
            "api": {"replicas": 0},
            "web": {"replicas": 0},
            "executionBroker": {"enabled": False},
            "releaseController": {"enabled": False},
            "migrations": {"enabled": False},
            "workers": {name: {"enabled": False} for name in ("outbox", "scheduler", "pipeline", "action")},
        },
    )
    return override, foundation


def _helm(
    args: argparse.Namespace,
    chart: Path,
    value_files: tuple[Path, ...],
    override: Path,
    phase_override: Path | None,
) -> dict[str, object]:
    command = [
        args.helm,
        "upgrade",
        "--install",
        _RELEASE,
        str(chart),
        "--namespace",
        args.namespace,
    ]
    for path in (*value_files, override):
        command.extend(("--values", str(path)))
    if phase_override is not None:
        command.extend(("--values", str(phase_override)))
    command.extend(("--atomic", "--wait", "--wait-for-jobs", "--timeout", "20m"))
    completed = subprocess.run(  # nosec B603 - validated Helm argv; remove if shell or free argv appears.
        command, check=False, capture_output=True, timeout=1300
    )
    if completed.returncode != 0:
        raise RuntimeError("macmini_qa_helm_deploy_failed")
    return {"phase": "foundation" if phase_override else "runtime", "returnCode": 0}


def _collect_evidence(args: argparse.Namespace) -> dict[str, object]:
    status = _json_command(args, ("get", "pods", "-o", "json"), "macmini_qa_pod_inventory_failed")
    helm_status = subprocess.run(  # nosec B603 - fixed Helm status argv; remove if shell or free argv appears.
        (args.helm, "status", _RELEASE, "--namespace", args.namespace, "--output", "json"),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if helm_status.returncode != 0:
        raise RuntimeError("macmini_qa_helm_status_failed")
    if len(helm_status.stdout) > 4 * 1024 * 1024:
        raise RuntimeError("macmini_qa_helm_status_failed")
    try:
        release = json.loads(helm_status.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_qa_helm_status_failed") from exc
    version = cast(dict[str, object], release).get("version") if isinstance(release, dict) else None
    if not isinstance(version, int) or isinstance(version, bool) or version < 2:
        raise RuntimeError("macmini_qa_helm_revision_unexpected")
    return {
        "helmRevision": version,
        "pods": _pod_summary(status),
        "migration": _migration_evidence(args, version),
    }


def _migration_evidence(args: argparse.Namespace, version: int) -> dict[str, object]:
    job_name = f"{_RELEASE}-migrate-{version}"
    result = _kubectl(args, ("logs", f"job/{job_name}", "-c", "migrate"), 60)
    if result.returncode != 0 or len(result.stdout) > 4 * 1024 * 1024:
        raise RuntimeError("macmini_qa_migration_evidence_missing")
    marker = _last_json_object(result.stdout)
    if marker != {"status": "passed", "runs": 2, "isIdempotent": True}:
        raise RuntimeError("macmini_qa_migration_evidence_invalid")
    return {
        "job": job_name,
        "runs": 2,
        "isIdempotent": True,
        "logSha256": "sha256:" + hashlib.sha256(result.stdout).hexdigest(),
        "rawLogStored": False,
    }


def _last_json_object(value: bytes) -> object:
    for raw_line in reversed(value.splitlines()):
        try:
            return json.loads(raw_line)
        except json.JSONDecodeError:
            continue
    return None


def _pod_summary(value: object) -> list[dict[str, object]]:
    items = value.get("items") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("macmini_qa_pod_inventory_failed")
    result: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata_value = item.get("metadata")
        status_value = item.get("status")
        metadata = cast(dict[str, object], metadata_value) if isinstance(metadata_value, dict) else {}
        status = cast(dict[str, object], status_value) if isinstance(status_value, dict) else {}
        result.append({"name": metadata.get("name"), "phase": status.get("phase")})
    return result


def _receipt(
    args: argparse.Namespace,
    manifest: dict[str, object],
    value_files: tuple[Path, ...],
    override: Path,
    secret_receipt: dict[str, object],
    foundation: dict[str, object],
    runtime: dict[str, object],
    evidence: dict[str, object],
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "runId": args.run_id,
        "recordedAt": utc_now(),
        "namespace": args.namespace,
        "gitRevision": manifest["revision"],
        "images": manifest["images"],
        "valuesSha256": _hash_paths((*value_files, override)),
        "initialAuthMode": "embedded_oauth_smoke",
        "secretBootstrapStatus": secret_receipt["status"],
        "foundation": foundation,
        "runtime": runtime,
        "evidence": evidence,
        "rawSecretsStored": False,
        "otherNamespacesMutated": False,
    }


def _ensure_namespace(args: argparse.Namespace) -> None:
    get = _kubectl(args, ("get", "namespace", args.namespace, "-o", "name"), 30)
    if get.returncode == 0:
        return
    create = _kubectl(args, ("create", "namespace", args.namespace), 30)
    if create.returncode != 0:
        raise RuntimeError("macmini_qa_namespace_create_failed")


def _assert_fresh_release(args: argparse.Namespace) -> None:
    status = subprocess.run(  # nosec B603 - fixed Helm status argv; remove if shell or free argv appears.
        (args.helm, "status", _RELEASE, "--namespace", args.namespace, "--output", "json"),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if status.returncode == 0:
        raise RuntimeError("macmini_qa_initial_deploy_release_exists")


def _json_command(args: argparse.Namespace, operation: tuple[str, ...], reason: str) -> object:
    result = _kubectl(args, operation, 60)
    if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
        raise RuntimeError(reason)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(reason) from exc


def _kubectl(
    args: argparse.Namespace,
    operation: tuple[str, ...],
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    command = (args.kubectl, "--kubeconfig", args.kubeconfig, *operation)
    return subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        command, check=False, capture_output=True, timeout=timeout
    )


def _qa_input_path(raw: str, *, is_directory: bool) -> Path:
    path = Path(raw).resolve()
    if QA_ROOT != path and QA_ROOT not in path.parents:
        raise ValueError("macmini_qa_deploy_input_outside_root")
    if (is_directory and not path.is_dir()) or (not is_directory and not path.is_file()):
        raise ValueError("macmini_qa_deploy_input_missing")
    return path


def _write_private_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _hash_paths(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--helm", default=str(QA_ROOT / "bin" / "helm"))
    parser.add_argument("--chart", required=True)
    parser.add_argument("--values", required=True)
    parser.add_argument("--initial-auth-values", required=True)
    parser.add_argument("--image-manifest", required=True)
    parser.add_argument("--age-recipient-file", required=True)
    receipt = deploy(parser.parse_args())
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
