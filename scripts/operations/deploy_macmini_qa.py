"""Install the immutable ARM64 release into sean1234's dedicated k3s namespace."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess  # nosec B404 - fixed Helm/kubectl only; remove if arbitrary command input is introduced.
import tempfile
from itertools import product
from pathlib import Path
from typing import Protocol, cast

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
_DOCKER = Path("/opt/homebrew/bin/docker")
_DOCKER_SOCKET = Path("/Users/sean1234/.colima/foundry-qa/docker.sock")
_MAX_DOCKER_OUTPUT = 8 * 1024 * 1024


class _Digest(Protocol):
    def update(self, value: bytes, /) -> None: ...


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
    image_prepull = _prepull_images(manifest, _qa_input_path(args.registry_token_file, is_directory=False))
    api_endpoint = _kubernetes_api_endpoint(args)
    override, foundation = _write_overrides(args.run_id, manifest, api_endpoint)
    value_files = (values, initial_auth_values)
    foundation_result = _helm(args, chart, value_files, override, foundation)
    final_result = _helm(args, chart, value_files, override, None)
    evidence = _collect_evidence(args)
    receipt = _receipt(
        args,
        manifest,
        value_files,
        override,
        secret_receipt,
        image_prepull,
        foundation_result,
        final_result,
        evidence,
    )
    target = QA_ROOT / "evidence" / args.run_id / "deployment-receipt.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt


def upgrade(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    if _RUN_ID.fullmatch(args.run_id) is None:
        raise ValueError("macmini_qa_run_id_invalid")
    ensure_qa_directories()
    chart = _qa_input_path(args.chart, is_directory=True)
    values = _qa_input_path(args.values, is_directory=False)
    manifest = _load_manifest(_qa_input_path(args.image_manifest, is_directory=False))
    token = _qa_input_path(args.registry_token_file, is_directory=False)
    _assert_deployed_release(args)
    runtime_contract = _write_upgrade_runtime_contract(args)
    image_prepull = _prepull_images(manifest, token)
    override = _write_runtime_override(args.run_id, manifest, _kubernetes_api_endpoint(args))
    helm_result = _helm_upgrade(args, chart, (values, runtime_contract), override)
    evidence = _collect_evidence(args)
    receipt = _upgrade_receipt(
        args,
        manifest,
        (values, runtime_contract),
        override,
        image_prepull,
        helm_result,
        evidence,
    )
    target = QA_ROOT / "evidence" / args.run_id / "upgrade-receipt.json"
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


def _prepull_images(manifest: dict[str, object], token_path: Path) -> dict[str, object]:
    _assert_docker_runtime()
    token = _read_private_token(token_path)
    images = cast(dict[str, dict[str, str]], manifest["images"])
    output_digest = hashlib.sha256()
    state = QA_ROOT / "state"
    with tempfile.TemporaryDirectory(prefix=".registry-auth-", dir=state) as raw_config:
        config = Path(raw_config)
        config.chmod(0o700)
        _docker_login(config, token, output_digest)
        for name in _IMAGE_NAMES:
            coordinate = f"{images[name]['repository']}@{images[name]['digest']}"
            _docker_pull(config, coordinate, str(manifest["revision"]), output_digest)
    return {
        "status": "passed",
        "count": len(_IMAGE_NAMES),
        "dockerSocket": str(_DOCKER_SOCKET),
        "outputSha256": "sha256:" + output_digest.hexdigest(),
        "rawCredentialStored": False,
    }


def _assert_docker_runtime() -> None:
    if not _DOCKER.is_file() or not os.access(_DOCKER, os.X_OK):
        raise RuntimeError("macmini_qa_docker_cli_invalid")
    try:
        socket_metadata = _DOCKER_SOCKET.stat()
    except OSError as exc:
        raise RuntimeError("macmini_qa_docker_socket_invalid") from exc
    if not stat.S_ISSOCK(socket_metadata.st_mode) or socket_metadata.st_uid != os.getuid():
        raise RuntimeError("macmini_qa_docker_socket_invalid")


def _read_private_token(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        value = stream.read(4097).strip()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError("macmini_qa_registry_token_invalid")
    if not 20 <= len(value) <= 4096 or any(character in value for character in b" \t\r\n"):
        raise RuntimeError("macmini_qa_registry_token_invalid")
    return value


def _docker_login(config: Path, token: bytes, output_digest: _Digest) -> None:
    result = _run_docker(
        config,
        ("login", "ghcr.io", "--username", "ludia8888", "--password-stdin"),
        timeout=60,
        input_bytes=token + b"\n",
    )
    _accept_docker_result(result, output_digest, "macmini_qa_registry_login_failed")


def _docker_pull(config: Path, coordinate: str, revision: str, output_digest: _Digest) -> None:
    pulled = _run_docker(config, ("pull", coordinate), timeout=1200)
    _accept_docker_result(pulled, output_digest, "macmini_qa_image_prepull_failed")
    inspected = _run_docker(config, ("image", "inspect", coordinate), timeout=60)
    _accept_docker_result(inspected, output_digest, "macmini_qa_image_inspect_failed")
    _validate_cached_image(_decode_image_inspection(inspected.stdout), coordinate, revision)


def _decode_image_inspection(payload: bytes) -> dict[str, object]:
    try:
        values = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_qa_image_inspect_failed") from exc
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise RuntimeError("macmini_qa_image_inspect_failed")
    return cast(dict[str, object], values[0])


def _validate_cached_image(value: dict[str, object], coordinate: str, revision: str) -> None:
    config = value.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    repo_digests = value.get("RepoDigests")
    expected = (
        value.get("Architecture") == "arm64",
        value.get("Os") == "linux",
        isinstance(labels, dict) and labels.get("org.opencontainers.image.revision") == revision,
        isinstance(repo_digests, list) and coordinate in repo_digests,
    )
    if not all(expected):
        raise RuntimeError("macmini_qa_image_inspect_failed")


def _run_docker(
    config: Path,
    operation: tuple[str, ...],
    *,
    timeout: float,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = (
        str(_DOCKER),
        "--config",
        str(config),
        "--host",
        f"unix://{_DOCKER_SOCKET}",
        *operation,
    )
    return subprocess.run(  # nosec B603 - fixed Docker CLI and dedicated foundry-qa socket only.
        command,
        input=input_bytes,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def _accept_docker_result(result: subprocess.CompletedProcess[bytes], output_digest: _Digest, reason: str) -> None:
    if result.returncode != 0 or len(result.stdout) + len(result.stderr) > _MAX_DOCKER_OUTPUT:
        raise RuntimeError(reason)
    output_digest.update(result.stdout)
    output_digest.update(result.stderr)


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


def _write_overrides(
    run_id: str, manifest: dict[str, object], api_endpoint: dict[str, object] | None = None
) -> tuple[Path, Path]:
    override = _write_runtime_override(run_id, manifest, api_endpoint)
    state = QA_ROOT / "state"
    foundation = state / f"{run_id}-foundation.json"
    _write_private_json(
        foundation,
        {
            "api": {"replicas": 0},
            "web": {"replicas": 0},
            "runtimePersistence": {"enabled": False},
            "executionBroker": {"enabled": False},
            "releaseController": {"enabled": False},
            "migrations": {"enabled": False},
            "workers": {name: {"enabled": False} for name in ("outbox", "scheduler", "pipeline", "action")},
        },
    )
    return override, foundation


def _write_runtime_override(
    run_id: str, manifest: dict[str, object], api_endpoint: dict[str, object] | None = None
) -> Path:
    override = QA_ROOT / "state" / f"{run_id}-immutable-images.json"
    value: dict[str, object] = {"global": {"revision": manifest["revision"]}, "images": manifest["images"]}
    if api_endpoint is not None:
        value["networkPolicy"] = api_endpoint
    _write_private_json(override, value)
    return override


def _kubernetes_api_endpoint(args: argparse.Namespace) -> dict[str, object]:
    payload = _json_command(
        args,
        ("get", "endpoints", "kubernetes", "--namespace", "default", "--output", "json"),
        "macmini_qa_kubernetes_api_endpoint_read_failed",
    )
    subsets = payload.get("subsets") if isinstance(payload, dict) else None
    if not isinstance(subsets, list):
        raise RuntimeError("macmini_qa_kubernetes_api_endpoint_invalid")
    candidates = _endpoint_candidates(subsets)
    if len(candidates) != 1:
        raise RuntimeError("macmini_qa_kubernetes_api_endpoint_invalid")
    address, port = candidates[0]
    parsed = ipaddress.ip_address(address)
    suffix = 32 if parsed.version == 4 else 128
    return {"kubernetesApiEndpointCidr": f"{parsed}/{suffix}", "kubernetesApiEndpointPort": port}


def _endpoint_candidates(subsets: list[object]) -> list[tuple[str, int]]:
    candidates: set[tuple[str, int]] = set()
    for subset in subsets:
        candidates.update(_subset_endpoint_candidates(subset))
    return sorted(candidates)


def _subset_endpoint_candidates(subset: object) -> set[tuple[str, int]]:
    addresses = subset.get("addresses") if isinstance(subset, dict) else None
    ports = subset.get("ports") if isinstance(subset, dict) else None
    if not isinstance(addresses, list) or not isinstance(ports, list):
        return set()
    pairs = product(_endpoint_field_values(addresses, "ip"), _endpoint_field_values(ports, "port"))
    return set(map(_cast_endpoint_pair, filter(_is_endpoint_tuple, pairs)))


def _endpoint_field_values(values: list[object], key: str) -> list[object]:
    return [item.get(key) for item in values if isinstance(item, dict)]


def _is_endpoint_tuple(value: tuple[object, object]) -> bool:
    return _is_endpoint_pair(value[0], value[1])


def _cast_endpoint_pair(value: tuple[object, object]) -> tuple[str, int]:
    return cast(tuple[str, int], value)


def _is_endpoint_pair(address: object, port: object) -> bool:
    return isinstance(address, str) and _is_endpoint_port(port)


def _is_endpoint_port(port: object) -> bool:
    return type(port) is int and port in range(1, 65536)


def _write_upgrade_runtime_contract(args: argparse.Namespace) -> Path:
    current = _current_helm_values(args)
    contract = _runtime_contract_values(current)
    override = QA_ROOT / "state" / f"{args.run_id}-runtime-contract.json"
    _write_private_json(override, contract)
    return override


def _current_helm_values(args: argparse.Namespace) -> dict[str, object]:
    result = subprocess.run(  # nosec B603 - fixed Helm get-values argv under the Mac mini guard.
        (
            args.helm,
            "get",
            "values",
            _RELEASE,
            "--namespace",
            args.namespace,
            "--kubeconfig",
            args.kubeconfig,
            "--all",
            "--output",
            "json",
        ),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
        raise RuntimeError("macmini_qa_upgrade_runtime_contract_read_failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_qa_upgrade_runtime_contract_read_failed") from exc
    if not isinstance(value, dict):
        raise RuntimeError("macmini_qa_upgrade_runtime_contract_read_failed")
    return cast(dict[str, object], value)


def _runtime_contract_values(current: dict[str, object]) -> dict[str, object]:
    global_values = _runtime_contract_mapping(current, "global")
    auth = _runtime_contract_mapping(current, "auth")
    mcp = _runtime_contract_mapping(current, "mcp")
    secrets = _runtime_contract_mapping(current, "secrets")
    external = _runtime_contract_mapping(current, "external")
    qa_dependencies = _runtime_contract_mapping(current, "qaDependencies")
    oidc = _runtime_contract_mapping(external, "oidc")
    keycloak = _runtime_contract_mapping(qa_dependencies, "keycloak")
    return {
        "global": {
            "protectedProfile": global_values.get("protectedProfile"),
            "runtimeProfile": global_values.get("runtimeProfile"),
        },
        "secrets": {"applicationExistingSecret": secrets.get("applicationExistingSecret")},
        "auth": auth,
        "mcp": mcp,
        "external": {"oidc": oidc},
        "qaDependencies": {"keycloak": {"publicBaseUrl": keycloak.get("publicBaseUrl")}},
    }


def _runtime_contract_mapping(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key)
    if not isinstance(value, dict):
        raise RuntimeError("macmini_qa_upgrade_runtime_contract_invalid")
    return cast(dict[str, object], value)


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


def _helm_upgrade(
    args: argparse.Namespace,
    chart: Path,
    value_files: tuple[Path, ...],
    override: Path,
) -> dict[str, object]:
    command = [
        args.helm,
        "upgrade",
        _RELEASE,
        str(chart),
        "--namespace",
        args.namespace,
        "--reset-then-reuse-values",
    ]
    for path in (*value_files, override):
        command.extend(("--values", str(path)))
    command.extend(("--atomic", "--wait", "--wait-for-jobs", "--timeout", "30m"))
    completed = subprocess.run(  # nosec B603 - validated Helm argv; remove if shell or free argv appears.
        command, check=False, capture_output=True, timeout=1900
    )
    if completed.returncode != 0:
        raise RuntimeError("macmini_qa_helm_upgrade_failed")
    return {"phase": "upgrade", "returnCode": 0}


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
    image_prepull: dict[str, object],
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
        "imagePrepull": image_prepull,
        "foundation": foundation,
        "runtime": runtime,
        "evidence": evidence,
        "rawSecretsStored": False,
        "otherNamespacesMutated": False,
    }


def _upgrade_receipt(
    args: argparse.Namespace,
    manifest: dict[str, object],
    value_files: tuple[Path, ...],
    override: Path,
    image_prepull: dict[str, object],
    helm_result: dict[str, object],
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
        "imagePrepull": image_prepull,
        "upgrade": helm_result,
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


def _assert_deployed_release(args: argparse.Namespace) -> None:
    status = subprocess.run(  # nosec B603 - fixed Helm status argv; remove if shell or free argv appears.
        (args.helm, "status", _RELEASE, "--namespace", args.namespace, "--output", "json"),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if status.returncode != 0:
        raise RuntimeError("macmini_qa_upgrade_release_missing")
    try:
        payload = json.loads(status.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_qa_upgrade_release_missing") from exc
    info = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(info, dict) or info.get("status") != "deployed":
        raise RuntimeError("macmini_qa_upgrade_release_not_deployed")


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
    command = (
        args.kubectl,
        "--kubeconfig",
        args.kubeconfig,
        "--namespace",
        args.namespace,
        *operation,
    )
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
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _assert_existing_private_json(path, payload)
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload.decode())


def _assert_existing_private_json(path: Path, expected: bytes) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RuntimeError("macmini_qa_private_json_invalid")
        if metadata.st_size > 1024 * 1024 or stream.read() != expected:
            raise RuntimeError("macmini_qa_private_json_conflict")


def _hash_paths(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--registry-token-file", required=True)
    deploy(parser.parse_args(argv))
    print('{"receiptStored": true, "status": "passed"}')
    return 0


def main_upgrade(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--helm", default=str(QA_ROOT / "bin" / "helm"))
    parser.add_argument("--chart", required=True)
    parser.add_argument("--values", required=True)
    parser.add_argument("--image-manifest", required=True)
    parser.add_argument("--registry-token-file", required=True)
    upgrade(parser.parse_args(argv))
    print('{"receiptStored": true, "status": "passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
