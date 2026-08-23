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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace

_MAX_API_BYTES = 2 * 1024 * 1024
_MAX_S3_MANIFEST_BYTES = 128 * 1024 * 1024
_MAX_S3_MANIFEST_ENTRIES = 250_000
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
_OPERATOR_TENANT_ID = "tenant-demo"
_OPERATOR_USER_ID = "enterprise-qa-operator"
_OPERATOR_ROLES = "admin,data_engineer,ops_manager"
_API_TIMEOUT_SECONDS = 180
_RESTORE_MODE_CONFIRM_DEADLINE_SECONDS = 180
_RESTORE_MODE_CONFIRM_INTERVAL_SECONDS = 2
_WORKER_RESTORE_ATTEMPTS = 3
_WORKER_RESTORE_INTERVAL_SECONDS = 2


class BackupProgress(TypedDict):
    isRestoreModeActive: bool
    workers: list[dict[str, object]]


class BackupOperationError(RuntimeError):
    """Safe operator failure with the cleanup result needed for automation."""

    def __init__(self, reason: str, cleanup: dict[str, object]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cleanup = cleanup


def backup(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    token = _private_text(Path(args.bearer_token_file))
    recipient = _age_recipient(Path(args.age_recipient_file))
    target = _backup_directory(args.run_id)
    restore_id = f"enterprise-qa-{args.run_id}"
    progress = BackupProgress(isRestoreModeActive=False, workers=[])
    try:
        return _capture_backup(args, token, recipient, target, restore_id, progress)
    except Exception as exc:
        cleanup = _safe_recover_failed_backup(args, token, target, restore_id, progress)
        raise BackupOperationError(_safe_failure_reason(exc), cleanup) from exc


def _capture_backup(
    args: argparse.Namespace,
    token: str,
    recipient: str,
    target: Path,
    restore_id: str,
    progress: BackupProgress,
) -> dict[str, object]:
    mode = _start_restore_mode(args.api_base_url, token, args.run_id, restore_id)
    if mode.get("status") != "paused":
        raise RuntimeError("macmini_backup_restore_mode_not_active")
    progress["isRestoreModeActive"] = True
    workers = _pause_workers(args, receipts=progress["workers"])
    release_values = _helm_release_values(args)
    _package_release_chart(args, target, release_values)
    artifact = _api_json(
        args.api_base_url,
        token,
        "/api/operations/backup-restore/artifacts",
        {"backupId": args.run_id},
    )
    preflight = _artifact_preflight_summary(artifact, expected_backup_id=args.run_id)
    before = _postgres_inventory(args)
    _pg_dump(args, target / "postgres.dump")
    s3_archive = target / "s3-versions.tar"
    _s3_archive(args, s3_archive)
    _s3_manifest_from_archive(s3_archive, target / "s3-manifest.json")
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


def _pause_workers(
    args: argparse.Namespace,
    *,
    receipts: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    result = _kubectl(args, ("get", "deployments", "-l", "app.kubernetes.io/name=foundry-lite", "-o", "json"), 30)
    payload = _json_command(result, "macmini_backup_worker_inventory_failed")
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("macmini_backup_worker_inventory_failed")
    receipts = receipts if receipts is not None else []
    for item in items:
        identity = _worker_identity(item)
        if identity is None:
            continue
        name, replicas = identity
        scaled = _kubectl(args, _replica_patch("deployment", name, 0), 30)
        if scaled.returncode != 0:
            raise RuntimeError("macmini_backup_worker_pause_failed")
        receipts.append({"name": name, "replicasBefore": replicas, "replicasAfter": 0})
    if not receipts:
        raise RuntimeError("macmini_backup_workers_missing")
    return receipts


def _artifact_preflight_summary(
    artifact: dict[str, object],
    *,
    expected_backup_id: str,
) -> dict[str, object]:
    status = _ready_preflight_status(artifact.get("preflightStatus"))
    backup_id = _matching_backup_id(artifact.get("backupId"), expected_backup_id)
    artifact_ref = _bounded_artifact_ref(artifact.get("artifactRef"))
    artifact_hash = _artifact_digest(artifact.get("artifactHash"))
    dataset_count = _nonnegative_receipt_int(artifact.get("datasetVersionCount"))
    issue_count = _zero_issue_count(artifact.get("issueCount"))
    payload_size = _nonnegative_receipt_int(artifact.get("payloadByteSize"))
    return {
        "schemaVersion": 1,
        "status": status,
        "backupId": backup_id,
        "datasetVersionCount": dataset_count,
        "issueCount": issue_count,
        "artifactRef": artifact_ref,
        "artifactHash": artifact_hash,
        "artifactPayloadByteSize": payload_size,
        "detailsStoredInImmutableArtifact": True,
    }


def _ready_preflight_status(value: object) -> str:
    if value != "ready":
        raise RuntimeError("macmini_backup_preflight_not_ready")
    return "ready"


def _matching_backup_id(value: object, expected: str) -> str:
    if value != expected:
        raise RuntimeError("macmini_backup_artifact_receipt_invalid")
    return expected


def _bounded_artifact_ref(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise RuntimeError("macmini_backup_artifact_receipt_invalid")
    return value


def _artifact_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise RuntimeError("macmini_backup_artifact_receipt_invalid")
    return value


def _nonnegative_receipt_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("macmini_backup_artifact_receipt_invalid")
    return value


def _zero_issue_count(value: object) -> int:
    if _nonnegative_receipt_int(value) != 0:
        raise RuntimeError("macmini_backup_preflight_not_ready")
    return 0


def _safe_recover_failed_backup(
    args: argparse.Namespace,
    token: str,
    target: Path,
    restore_id: str,
    progress: BackupProgress,
) -> dict[str, object]:
    try:
        return _recover_failed_backup(args, token, target, restore_id, progress)
    except Exception:
        errors = ["macmini_backup_cleanup_unhandled"]
        files_removed = _remove_partial_backup(target, args.run_id, errors)
        return {
            "status": "failed",
            "postRestoreValidationStatus": "unknown",
            "restoreModeStatus": "unknown",
            "workersRestored": False,
            "workerCount": len(progress["workers"]),
            "partialFilesRemoved": files_removed,
            "errors": errors,
        }


def _recover_failed_backup(
    args: argparse.Namespace,
    token: str,
    target: Path,
    restore_id: str,
    progress: BackupProgress,
) -> dict[str, object]:
    errors: list[str] = []
    validation_status = "not_required"
    resume_status = "not_required"
    if progress["isRestoreModeActive"]:
        validation_status, resume_status = _approve_failed_backup_resume(args, token, restore_id, errors)
    can_restore_workers = not progress["isRestoreModeActive"] or resume_status == "resume_approved"
    workers_restored = _restore_paused_workers(args, progress["workers"], errors) if can_restore_workers else False
    files_removed = _remove_partial_backup(target, args.run_id, errors)
    status = "passed" if not errors and workers_restored and files_removed else "failed"
    return {
        "status": status,
        "postRestoreValidationStatus": validation_status,
        "restoreModeStatus": resume_status,
        "workersRestored": workers_restored,
        "workerCount": len(progress["workers"]),
        "partialFilesRemoved": files_removed,
        "errors": errors,
    }


def _approve_failed_backup_resume(
    args: argparse.Namespace,
    token: str,
    restore_id: str,
    errors: list[str],
) -> tuple[str, str]:
    validation_id = f"failed-backup-{args.run_id}"
    try:
        validation = _api_json(
            args.api_base_url,
            token,
            f"/api/operations/backup-restore/restore-mode/{restore_id}/post-restore-validation",
            {"validationId": validation_id},
        )
        validation_status = str(validation.get("status", "unknown"))
        if validation_status != "passed":
            errors.append("macmini_backup_cleanup_validation_failed")
            return validation_status, "not_run"
        resumed = _api_json(
            args.api_base_url,
            token,
            f"/api/operations/backup-restore/restore-mode/{restore_id}/approve-resume",
            {"validationId": validation_id},
        )
        resume_status = str(resumed.get("status", "unknown"))
        if resume_status != "resume_approved":
            errors.append("macmini_backup_cleanup_resume_failed")
        return validation_status, resume_status
    except (RuntimeError, ValueError):
        errors.append("macmini_backup_cleanup_api_failed")
        return "failed", "not_run"


def _restore_paused_workers(
    args: argparse.Namespace,
    workers: list[dict[str, object]],
    errors: list[str],
) -> bool:
    for worker in workers:
        identity = _paused_worker_identity(worker)
        if identity is None:
            errors.append("macmini_backup_cleanup_worker_receipt_invalid")
            continue
        name, replicas = identity
        try:
            restored = _restore_worker_replicas(args, name, replicas)
        except (OSError, subprocess.SubprocessError):
            restored = False
        if not restored:
            errors.append(f"macmini_backup_cleanup_worker_restore_failed:{name}")
    return not any(error.startswith("macmini_backup_cleanup_worker_") for error in errors)


def _paused_worker_identity(value: object) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    replicas = value.get("replicasBefore")
    if not isinstance(name, str) or not name.startswith("foundry-lite-worker-"):
        return None
    if not isinstance(replicas, int) or isinstance(replicas, bool) or not 0 <= replicas <= 10:
        return None
    return name, replicas


def _restore_worker_replicas(args: argparse.Namespace, name: str, replicas: int) -> bool:
    for attempt in range(_WORKER_RESTORE_ATTEMPTS):
        result = _kubectl(args, _replica_patch("deployment", name, replicas), 30)
        if result.returncode == 0:
            if replicas == 0:
                return True
            rollout = _kubectl(args, ("rollout", "status", f"deployment/{name}", "--timeout=300s"), 320)
            return rollout.returncode == 0
        if attempt + 1 < _WORKER_RESTORE_ATTEMPTS:
            time.sleep(_WORKER_RESTORE_INTERVAL_SECONDS)
    return False


def _remove_partial_backup(target: Path, run_id: str, errors: list[str]) -> bool:
    candidates = (
        target.parent / f"{run_id}.tar",
        target.parent / f"{run_id}.tar.age",
        target.parent / f"{run_id}-backup-receipt.json",
    )
    try:
        shutil.rmtree(target, ignore_errors=True)
        for path in candidates:
            path.unlink(missing_ok=True)
    except OSError:
        errors.append("macmini_backup_cleanup_files_failed")
    return not target.exists() and all(not path.exists() for path in candidates)


def _safe_failure_reason(exc: Exception) -> str:
    reason = str(exc)
    if re.fullmatch(r"[a-z0-9_.:-]{1,160}", reason):
        return reason
    return "macmini_backup_failed_unclassified"


def _failure_receipt(run_id: str, error: BackupOperationError) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "failed",
        "runId": run_id,
        "failedAt": datetime.now(UTC).isoformat(),
        "reason": error.reason,
        "cleanup": error.cleanup,
        "rawSecretsStored": False,
    }


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


def _replica_patch(kind: str, name: str, replicas: int) -> tuple[str, ...]:
    payload = json.dumps({"spec": {"replicas": replicas}}, separators=(",", ":"))
    return (
        "patch",
        kind,
        name,
        "--type=merge",
        "--field-manager=helm",
        "-p",
        payload,
    )


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


def _s3_archive(args: argparse.Namespace, output: Path) -> None:
    command = _kubectl_argv(args, _api_exec_operation("export"))
    with output.open("xb") as stream:
        os.chmod(output, 0o600)
        result = subprocess.run(  # nosec B603 - validated kubectl argv; remove if shell or free argv appears.
            command, check=False, stdout=stream, stderr=subprocess.PIPE, timeout=3600
        )
    if result.returncode != 0 or output.stat().st_size == 0:
        raise RuntimeError("macmini_backup_s3_archive_failed")


def _s3_manifest_from_archive(archive_path: Path, output: Path) -> None:
    raw = _read_s3_manifest(archive_path)
    _validate_s3_manifest(raw)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def _read_s3_manifest(archive_path: Path) -> bytes:
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            member = archive.getmember("manifest.json")
            if not member.isfile() or not 0 < member.size <= _MAX_S3_MANIFEST_BYTES:
                raise RuntimeError("macmini_backup_s3_manifest_invalid")
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("macmini_backup_s3_manifest_invalid")
            raw = stream.read(_MAX_S3_MANIFEST_BYTES + 1)
    except (KeyError, OSError, tarfile.TarError) as exc:
        raise RuntimeError("macmini_backup_s3_manifest_invalid") from exc
    if not 0 < len(raw) <= _MAX_S3_MANIFEST_BYTES:
        raise RuntimeError("macmini_backup_s3_manifest_invalid")
    return raw


def _validate_s3_manifest(raw: bytes) -> None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_backup_s3_manifest_invalid") from exc
    entries = payload.get("entries") if isinstance(payload, dict) else None
    bucket = payload.get("bucket") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != 1
        or not isinstance(bucket, str)
        or not bucket
        or not isinstance(entries, list)
        or len(entries) > _MAX_S3_MANIFEST_ENTRIES
    ):
        raise RuntimeError("macmini_backup_s3_manifest_invalid")


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


def _start_restore_mode(base_url: str, token: str, backup_id: str, restore_id: str) -> dict[str, object]:
    try:
        return _api_json(
            base_url,
            token,
            "/api/operations/backup-restore/restore-mode/start",
            {"backupId": backup_id, "restoreId": restore_id},
        )
    except RuntimeError as exc:
        if str(exc) != "macmini_backup_api_unavailable":
            raise
        return _confirm_started_restore_mode(base_url, token, backup_id, restore_id, exc)


def _confirm_started_restore_mode(
    base_url: str,
    token: str,
    backup_id: str,
    restore_id: str,
    original_error: RuntimeError,
) -> dict[str, object]:
    deadline = time.monotonic() + _RESTORE_MODE_CONFIRM_DEADLINE_SECONDS
    while True:
        try:
            status = _api_get_json(base_url, token, f"/api/operations/backup-restore/restore-mode/{restore_id}")
        except RuntimeError:
            status = {}
        if status.get("status") == "paused" and status.get("backupId") == backup_id:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("macmini_backup_restore_mode_start_not_confirmed") from original_error
        time.sleep(min(_RESTORE_MODE_CONFIRM_INTERVAL_SECONDS, remaining))


def _api_json(base_url: str, token: str, path: str, payload: object) -> dict[str, object]:
    request = _api_request(base_url, token, path, payload=payload)
    return _api_response_json(request)


def _api_get_json(base_url: str, token: str, path: str) -> dict[str, object]:
    request = _api_request(base_url, token, path, payload=None)
    return _api_response_json(request)


def _api_request(base_url: str, token: str, path: str, *, payload: object | None) -> urllib.request.Request:
    base = urllib.parse.urlsplit(base_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("macmini_backup_api_url_invalid")
    if base.scheme == "http" and base.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("macmini_backup_api_http_not_loopback")
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if urllib.parse.urlsplit(url).netloc != base.netloc:
        raise ValueError("macmini_backup_api_origin_mismatch")
    return urllib.request.Request(
        url,
        data=(json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None),
        method="POST" if payload is not None else "GET",
        headers=_operator_api_headers(token),
    )


def _api_response_json(request: urllib.request.Request) -> dict[str, object]:
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=_API_TIMEOUT_SECONDS) as response:
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


def _operator_api_headers(token: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "accept": "application/json",
        "X-Tenant-ID": _OPERATOR_TENANT_ID,
        "X-User-ID": _OPERATOR_USER_ID,
        "X-Roles": _OPERATOR_ROLES,
    }


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
    args = parser.parse_args()
    try:
        receipt = backup(args)
    except BackupOperationError as exc:
        print(json.dumps(_failure_receipt(args.run_id, exc), sort_keys=True))
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
