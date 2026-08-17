"""Create one quiesced, encrypted PostgreSQL and versioned-S3 QA backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed operator tools only; remove if arbitrary command input is introduced.
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace

_MAX_API_BYTES = 2 * 1024 * 1024
_S3_SNAPSHOT_SCRIPT = "/app/scripts/operations/s3_version_snapshot.py"
_WORKER_COMPONENT_PREFIX = "worker-"
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
_CHART_RELATIVE_PATH = Path("deploy/helm/foundry-lite")


def backup(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    token = _private_text(Path(args.bearer_token_file))
    recipient = _age_recipient(Path(args.age_recipient_file))
    target = _backup_directory(args.run_id)
    restore_id = f"enterprise-qa-{args.run_id}"
    mode = _api_json(
        args.api_base_url,
        token,
        "/api/operations/backup-restore/restore-mode/start",
        {"backupId": args.run_id, "restoreId": restore_id},
    )
    if mode.get("status") != "paused":
        raise RuntimeError("macmini_backup_restore_mode_not_active")
    workers = _pause_workers(args)
    before = _postgres_inventory(args)
    release_values = _helm_release_values(args)
    _package_release_chart(args, target, release_values)
    preflight = _api_json(
        args.api_base_url,
        token,
        "/api/operations/backup-restore/preflight",
        {"backupId": args.run_id},
    )
    artifact = _api_json(
        args.api_base_url,
        token,
        "/api/operations/backup-restore/artifacts",
        {"backupId": args.run_id},
    )
    _pg_dump(args, target / "postgres.dump")
    _s3_manifest(args, target / "s3-manifest.json")
    _s3_archive(args, target / "s3-versions.tar")
    _kubernetes_images(args, target / "kubernetes-images.json")
    after = _postgres_inventory(args)
    if before != after:
        raise RuntimeError("macmini_backup_commit_point_drift")
    if release_values != _helm_release_values(args):
        raise RuntimeError("macmini_backup_release_values_drift")
    _write_json(target / "database-inventory.json", before)
    _write_json(target / "helm-release-values.json", release_values)
    _write_json(target / "restore-mode.json", mode)
    _write_json(target / "platform-preflight.json", preflight)
    _write_json(target / "platform-artifact.json", artifact)
    _write_json(target / "paused-workers.json", workers)
    manifest = _file_manifest(target)
    _write_json(target / "SHA256MANIFEST.json", manifest)
    archive = target.parent / f"{args.run_id}.tar"
    encrypted = target.parent / f"{args.run_id}.tar.age"
    _tar_directory(target, archive)
    archive_sha = _hash_file(archive)
    _encrypt(args.age, recipient, archive, encrypted)
    encrypted_sha = _hash_file(encrypted)
    archive.unlink()
    receipt = {
        "schemaVersion": 1,
        "status": "passed",
        "runId": args.run_id,
        "restoreId": restore_id,
        "createdAt": datetime.now(UTC).isoformat(),
        "archive": str(encrypted),
        "plaintextArchiveSha256": archive_sha,
        "encryptedArchiveSha256": encrypted_sha,
        "postgresqlCommitPointStable": True,
        "s3VersionAndContentHashesCaptured": True,
        "exactHelmReleaseValuesCaptured": True,
        "exactHelmChartCaptured": True,
        "restoreModeRemainsActive": True,
        "workersRemainPaused": True,
        "rawTokensStored": False,
        "plaintextFilesRemoved": True,
    }
    _write_json(target.parent / f"{args.run_id}-backup-receipt.json", receipt)
    shutil.rmtree(target)
    return receipt


def _pause_workers(args: argparse.Namespace) -> list[dict[str, object]]:
    result = _kubectl(args, ("get", "deployments", "-l", "app.kubernetes.io/name=foundry-lite", "-o", "json"), 30)
    payload = _json_command(result, "macmini_backup_worker_inventory_failed")
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("macmini_backup_worker_inventory_failed")
    receipts: list[dict[str, object]] = []
    for item in items:
        identity = _worker_identity(item)
        if identity is None:
            continue
        name, replicas = identity
        scaled = _kubectl(args, ("scale", "deployment", name, "--replicas=0"), 30)
        if scaled.returncode != 0:
            raise RuntimeError("macmini_backup_worker_pause_failed")
        receipts.append({"name": name, "replicasBefore": replicas, "replicasAfter": 0})
    if not receipts:
        raise RuntimeError("macmini_backup_workers_missing")
    return receipts


def _worker_identity(value: object) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    metadata = value.get("metadata")
    spec = value.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        return None
    labels = metadata.get("labels")
    component = labels.get("app.kubernetes.io/component") if isinstance(labels, dict) else None
    name = metadata.get("name")
    replicas = spec.get("replicas", 1)
    if not isinstance(component, str) or not component.startswith(_WORKER_COMPONENT_PREFIX):
        return None
    if not isinstance(name, str) or not isinstance(replicas, int) or isinstance(replicas, bool):
        raise RuntimeError("macmini_backup_worker_inventory_invalid")
    return name, replicas


def _postgres_inventory(args: argparse.Namespace) -> object:
    query = (
        "SELECT json_build_object("
        "'schemaRevision',(SELECT version_num FROM alembic_version LIMIT 1),"
        "'auditEvents',(SELECT count(*) FROM audit_events),"
        "'outboxEvents',(SELECT count(*) FROM outbox_events),"
        "'actionRuns',(SELECT count(*) FROM action_runs),"
        "'datasetVersions',(SELECT count(*) FROM dataset_versions));"
    )
    command = (
        "exec",
        "statefulset/foundry-lite-postgresql",
        "--",
        "psql",
        "-U",
        "postgres",
        "-d",
        "foundry_lite",
        "-At",
        "-c",
        query,
    )
    return _json_command(_kubectl(args, command, 60), "macmini_backup_inventory_failed")


def _pg_dump(args: argparse.Namespace, output: Path) -> None:
    command = _kubectl_argv(
        args,
        (
            "exec",
            "statefulset/foundry-lite-postgresql",
            "--",
            "pg_dump",
            "-U",
            "postgres",
            "-d",
            "foundry_lite",
            "--format=custom",
            "--no-owner",
            "--no-acl",
        ),
    )
    with output.open("xb") as stream:
        os.chmod(output, 0o600)
        result = subprocess.run(  # nosec B603 - validated kubectl argv; remove if shell or free argv appears.
            command, check=False, stdout=stream, stderr=subprocess.PIPE, timeout=1800
        )
    if result.returncode != 0 or output.stat().st_size == 0:
        raise RuntimeError("macmini_backup_pg_dump_failed")


def _s3_manifest(args: argparse.Namespace, output: Path) -> None:
    operation = _api_exec_operation("manifest")
    result = _kubectl(args, operation, 1800)
    payload = _json_command(result, "macmini_backup_s3_manifest_failed")
    _write_json(output, payload)


def _s3_archive(args: argparse.Namespace, output: Path) -> None:
    command = _kubectl_argv(args, _api_exec_operation("export"))
    with output.open("xb") as stream:
        os.chmod(output, 0o600)
        result = subprocess.run(  # nosec B603 - validated kubectl argv; remove if shell or free argv appears.
            command, check=False, stdout=stream, stderr=subprocess.PIPE, timeout=3600
        )
    if result.returncode != 0 or output.stat().st_size == 0:
        raise RuntimeError("macmini_backup_s3_archive_failed")


def _api_exec_operation(mode: str) -> tuple[str, ...]:
    return (
        "exec",
        "deployment/foundry-lite",
        "-c",
        "api",
        "--",
        "/opt/foundry-lite-venv/bin/python",
        _S3_SNAPSHOT_SCRIPT,
        mode,
    )


def _kubernetes_images(args: argparse.Namespace, output: Path) -> None:
    result = _kubectl(args, ("get", "pods", "-o", "json"), 30)
    payload = _json_command(result, "macmini_backup_pod_inventory_failed")
    images: list[dict[str, object]] = []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("macmini_backup_pod_inventory_failed")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("status"), dict):
            continue
        metadata = _dict_or_empty(item.get("metadata"))
        statuses = item["status"].get("containerStatuses", [])
        for status in statuses if isinstance(statuses, list) else []:
            if isinstance(status, dict):
                images.append(
                    {"pod": metadata.get("name"), "name": status.get("name"), "imageID": status.get("imageID")}
                )
    _write_json(output, {"schemaVersion": 1, "images": images})


def _helm_release_values(args: argparse.Namespace) -> dict[str, object]:
    result = subprocess.run(  # nosec B603 - fixed Helm read-only argv; remove if free argv appears.
        (
            args.helm,
            "get",
            "values",
            "foundry-lite",
            "--namespace",
            args.namespace,
            "--all",
            "--output",
            "json",
        ),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0 or len(result.stdout) > 4 * 1024 * 1024:
        raise RuntimeError("macmini_backup_helm_values_failed")
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_backup_helm_values_invalid") from exc
    return _validated_release_values(values)


def _package_release_chart(
    args: argparse.Namespace,
    target: Path,
    release_values: dict[str, object],
) -> Path:
    chart = _release_chart_path(args.chart)
    revision = _dict_or_empty(release_values.get("global")).get("revision")
    repository = QA_ROOT / "repo"
    head = subprocess.run(  # nosec B603 - fixed read-only Git argv; remove if free argv appears.
        (args.git, "-C", str(repository), "rev-parse", "HEAD"),
        check=False,
        capture_output=True,
        timeout=30,
    )
    status = subprocess.run(  # nosec B603 - fixed read-only Git argv; remove if free argv appears.
        (
            args.git,
            "-C",
            str(repository),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            str(_CHART_RELATIVE_PATH),
        ),
        check=False,
        capture_output=True,
        timeout=30,
    )
    if head.returncode != 0 or head.stdout.decode().strip() != revision or status.returncode != 0 or status.stdout:
        raise RuntimeError("macmini_backup_chart_source_not_exact_release")
    packaged = subprocess.run(  # nosec B603 - fixed Helm package argv; remove if free argv appears.
        (args.helm, "package", str(chart), "--destination", str(target)),
        check=False,
        capture_output=True,
        timeout=60,
    )
    candidates = list(target.glob("foundry-lite-*.tgz"))
    if packaged.returncode != 0 or len(candidates) != 1 or candidates[0].stat().st_size == 0:
        raise RuntimeError("macmini_backup_chart_package_failed")
    destination = target / "helm-chart.tgz"
    candidates[0].replace(destination)
    os.chmod(destination, 0o600)
    return destination


def _release_chart_path(raw_path: str) -> Path:
    path = Path(raw_path)
    expected = (QA_ROOT / "repo" / _CHART_RELATIVE_PATH).resolve()
    if path.is_symlink():
        raise ValueError("macmini_backup_chart_path_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("macmini_backup_chart_path_invalid") from exc
    if resolved != expected or not resolved.is_dir():
        raise ValueError("macmini_backup_chart_path_invalid")
    return resolved


def _validated_release_values(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("macmini_backup_helm_values_invalid")
    global_values = _dict_or_empty(value.get("global"))
    images = _dict_or_empty(value.get("images"))
    auth = _dict_or_empty(value.get("auth"))
    qa_dependencies = _dict_or_empty(value.get("qaDependencies"))
    image_values = {name: _dict_or_empty(images.get(name)) for name in _IMAGE_NAMES}
    expected = (
        _REVISION.fullmatch(str(global_values.get("revision", ""))) is not None,
        _has_pull_secret(global_values.get("imagePullSecrets")),
        set(images) == set(_IMAGE_NAMES),
        all(_DIGEST.fullmatch(str(item.get("digest", ""))) is not None for item in image_values.values()),
        all(image_values[name].get("repository") == _IMAGE_REPOSITORIES[name] for name in _IMAGE_NAMES),
        auth.get("profile") in {"header-trust", "oidc"},
        qa_dependencies.get("enabled") is True,
    )
    if not all(expected):
        raise RuntimeError("macmini_backup_helm_values_invalid")
    return value


def _has_pull_secret(value: object) -> bool:
    return isinstance(value, list) and "foundry-lite-ghcr" in value


def _api_json(base_url: str, token: str, path: str, payload: object) -> dict[str, object]:
    base = urllib.parse.urlsplit(base_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("macmini_backup_api_url_invalid")
    if base.scheme == "http" and base.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("macmini_backup_api_http_not_loopback")
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if urllib.parse.urlsplit(url).netloc != base.netloc:
        raise ValueError("macmini_backup_api_origin_mismatch")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        method="POST",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json", "accept": "application/json"},
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(_MAX_API_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("macmini_backup_api_unavailable") from exc
    if len(body) > _MAX_API_BYTES:
        raise RuntimeError("macmini_backup_api_response_too_large")
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_backup_api_response_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("macmini_backup_api_response_invalid")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, fp: object, code: int, msg: str, headers: object, url: str) -> None:
        raise RuntimeError("macmini_backup_api_redirect_not_allowed")


def _kubectl(
    args: argparse.Namespace,
    operation: tuple[str, ...],
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        _kubectl_argv(args, operation), check=False, capture_output=True, timeout=timeout
    )


def _kubectl_argv(args: argparse.Namespace, operation: tuple[str, ...]) -> tuple[str, ...]:
    return (args.kubectl, "--kubeconfig", args.kubeconfig, "--namespace", args.namespace, *operation)


def _json_command(result: subprocess.CompletedProcess[bytes], reason: str) -> object:
    if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
        raise RuntimeError(reason)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(reason) from exc


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _file_manifest(directory: Path) -> dict[str, object]:
    files = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        files.append(
            {"path": str(path.relative_to(directory)), "size": path.stat().st_size, "sha256": _hash_file(path)}
        )
    return {"schemaVersion": 1, "files": files}


def _tar_directory(directory: Path, output: Path) -> None:
    with tarfile.open(output, mode="x") as archive:
        archive.add(directory, arcname=directory.name, recursive=True)
    os.chmod(output, 0o600)


def _encrypt(age: str, recipient: str, source: Path, output: Path) -> None:
    command = (age, "-r", recipient, "-o", str(output), str(source))
    result = subprocess.run(  # nosec B603 - fixed age argv; remove if arbitrary recipient commands appear.
        command, check=False, capture_output=True, timeout=3600
    )
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("macmini_backup_age_encryption_failed")
    os.chmod(output, 0o600)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _private_text(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise ValueError("macmini_backup_private_file_permissions_invalid")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("macmini_backup_private_file_empty")
    return value


def _age_recipient(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value.startswith("age1") or len(value) > 200:
        raise ValueError("macmini_backup_age_recipient_invalid")
    return value


def _backup_directory(run_id: str) -> Path:
    if not run_id or not all(value.isalnum() or value in "-_" for value in run_id):
        raise ValueError("macmini_backup_run_id_invalid")
    target = QA_ROOT / "backups" / run_id
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    return target


def _write_json(path: Path, payload: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--helm", default=str(QA_ROOT / "bin" / "helm"))
    parser.add_argument("--git", default="git")
    parser.add_argument("--chart", default=str(QA_ROOT / "repo" / _CHART_RELATIVE_PATH))
    parser.add_argument("--api-base-url", default="http://127.0.0.1:30443")
    parser.add_argument("--bearer-token-file", required=True)
    parser.add_argument("--age-recipient-file", required=True)
    parser.add_argument("--age", default=str(QA_ROOT / "bin" / "age"))
    receipt = backup(parser.parse_args())
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
