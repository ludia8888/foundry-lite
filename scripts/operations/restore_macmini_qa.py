"""Restore an encrypted QA backup into the isolated recovery namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed age/Helm/kubectl only; remove if arbitrary commands are introduced.
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from scripts.operations.backup_macmini_qa import _operator_api_headers, _validated_release_values
from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace

_RECOVERY_NAMESPACE = "foundry-qa-recovery"
_SECRETS = (
    "foundry-lite-application",
    "foundry-lite-runtime-application",
    "foundry-lite-migration",
    "foundry-lite-oauth-signing",
    "foundry-lite-qa-dependencies",
    "foundry-lite-backup-age",
    "foundry-lite-ghcr",
)
_APP_DEPLOYMENTS = (
    ("foundry-lite", 2),
    ("foundry-lite-web", 2),
    ("foundry-lite-execution-broker", 2),
    ("foundry-lite-release-controller", 1),
    ("foundry-lite-worker-outbox", 1),
    ("foundry-lite-worker-scheduler", 1),
    ("foundry-lite-worker-pipeline", 1),
    ("foundry-lite-worker-action", 1),
)
_SOURCE_CAPACITY_HANDOFF = (
    ("statefulset", "foundry-lite-clamav", 0),
    ("statefulset", "foundry-lite-elasticsearch", 0),
    ("statefulset", "foundry-lite-grafana", 0),
    ("statefulset", "foundry-lite-keycloak", 0),
    ("statefulset", "foundry-lite-prometheus", 0),
    ("statefulset", "foundry-lite-redpanda", 0),
    ("statefulset", "foundry-lite-tempo", 0),
    ("deployment", "foundry-lite-temporal", 0),
    ("deployment", "foundry-lite-web", 0),
    ("deployment", "foundry-lite-execution-broker", 0),
    ("deployment", "foundry-lite-release-controller", 0),
    ("deployment", "foundry-lite", 1),
)
_RECOVERY_HIBERNATE = (
    *(("deployment", name) for name, _replicas in _APP_DEPLOYMENTS),
    ("deployment", "foundry-lite-temporal"),
    *(
        ("statefulset", f"foundry-lite-{name}")
        for name in (
            "clamav",
            "elasticsearch",
            "grafana",
            "keycloak",
            "minio",
            "postgresql",
            "prometheus",
            "redpanda",
            "tempo",
        )
    ),
)
_MAX_API_BYTES = 2 * 1024 * 1024
_KUBERNETES_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
_STORAGE_SIZE = re.compile(r"^[1-9][0-9]*(?:Mi|Gi)$")
_API_TIMEOUT_SECONDS = 300
_API_REQUEST_ATTEMPTS = 2
_S3_IMPORT_ATTEMPTS = 2


class CapacityReceipt(TypedDict):
    kind: str
    name: str
    replicasBefore: int
    replicasDuringRecovery: int


class S3RestoreReceipt(TypedDict):
    attempts: int
    purgedVersionCount: int


def restore(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.source_namespace)
    assert_namespace(args.recovery_namespace)
    if args.recovery_namespace != _RECOVERY_NAMESPACE:
        raise ValueError("macmini_restore_recovery_namespace_required")
    started = time.monotonic()
    token = _private_text(Path(args.bearer_token_file))
    identity = _private_path(Path(args.age_identity_file))
    archive = _backup_archive(args.run_id)
    _verify_encrypted_archive(args.run_id, archive)
    temporary = Path(tempfile.mkdtemp(prefix="restore-", dir=QA_ROOT / "state"))
    os.chmod(temporary, 0o700)
    source_capacity: list[CapacityReceipt] = []
    payload: Path | None = None
    is_source_resume_approved = False
    try:
        payload = _decrypt_and_extract(args.age, identity, archive, temporary, args.run_id)
        _verify_manifest(payload)
        _ensure_namespace(args)
        for secret in _SECRETS:
            _copy_secret(args, secret)
        release_values = _archived_release_values(payload)
        release_chart = _archived_release_chart(payload)
        source_capacity = _handoff_source_capacity(args)
        _install_recovery(args, temporary, release_values, release_chart, is_foundation=True)
        _restore_postgresql(args, payload / "postgres.dump")
        _ensure_recovery_runtime_pvc(args, release_values)
        _scale_named(args, args.recovery_namespace, "foundry-lite", 1)
        _wait_deployment(args, args.recovery_namespace, "foundry-lite", 300)
        s3_restore = _restore_s3(args, payload / "s3-versions.tar")
        observed = _postgres_inventory(args, args.recovery_namespace)
        expected = json.loads((payload / "database-inventory.json").read_text(encoding="utf-8"))
        if observed != expected:
            raise RuntimeError("macmini_restore_database_inventory_mismatch")
        _install_recovery(args, temporary, release_values, release_chart, is_foundation=False)
        _wait_all_recovery(args)
        restore_id = f"enterprise-qa-{args.run_id}"
        with _port_forward(args, args.recovery_namespace, 18081):
            _recovery_validation, recovery_resume = _validate_and_approve_resume(
                "http://127.0.0.1:18081",
                token,
                restore_id,
                f"recovery-{args.run_id}",
            )
        with _port_forward(args, args.source_namespace, 18082):
            _source_validation, source_resume = _validate_and_approve_resume(
                "http://127.0.0.1:18082",
                token,
                restore_id,
                f"source-{args.run_id}",
            )
            is_source_resume_approved = True
        _hibernate_recovery(args)
        _restore_source_capacity(args, source_capacity)
        _resume_source_workers(args, payload)
        elapsed = int(time.monotonic() - started)
        receipt = {
            "schemaVersion": 1,
            "status": "passed" if elapsed <= 1800 else "failed",
            "runId": args.run_id,
            "restoredAt": datetime.now(UTC).isoformat(),
            "rtoSeconds": elapsed,
            "rtoTargetSeconds": 1800,
            "rpo": 0,
            "databaseInventoryMatched": True,
            "s3ContentCoordinatesMatched": True,
            "s3ImportAttempts": s3_restore["attempts"],
            "recoveryS3VersionsPurged": s3_restore["purgedVersionCount"],
            "exactHelmReleaseValuesRestored": True,
            "exactHelmChartRestored": True,
            "twoPhaseRecoveryInstall": True,
            "recoveryResumeStatus": recovery_resume.get("status"),
            "sourceResumeStatus": source_resume.get("status"),
            "sourceWorkersResumed": True,
            "sourceCapacityHandoff": source_capacity,
            "sourceCapacityRestored": True,
            "recoveryHibernatedAfterValidation": True,
            "originalNamespaceOverwritten": False,
        }
        _write_receipt(args.run_id, receipt)
        return receipt
    except BaseException:
        if source_capacity:
            _best_effort_capacity_recovery(args, source_capacity)
        if is_source_resume_approved and payload is not None:
            _best_effort_source_worker_recovery(args, payload)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _decrypt_and_extract(age: str, identity: Path, archive: Path, temporary: Path, run_id: str) -> Path:
    plain = temporary / "backup.tar"
    command = (age, "--decrypt", "-i", str(identity), "-o", str(plain), str(archive))
    result = subprocess.run(  # nosec B603 - fixed age argv; remove if shell or free argv appears.
        command, check=False, capture_output=True, timeout=3600
    )
    if result.returncode != 0 or not plain.is_file():
        raise RuntimeError("macmini_restore_age_decryption_failed")
    os.chmod(plain, 0o600)
    extracted = temporary / "payload"
    extracted.mkdir(mode=0o700)
    with tarfile.open(plain, mode="r") as source:
        _safe_extract(source, extracted)
    plain.unlink()
    payload = extracted / run_id
    if not payload.is_dir():
        raise RuntimeError("macmini_restore_payload_missing")
    return payload


def _verify_encrypted_archive(run_id: str, archive: Path) -> None:
    receipt_path = QA_ROOT / "backups" / f"{run_id}-backup-receipt.json"
    receipt = json.loads(_private_path(receipt_path).read_text(encoding="utf-8"))
    expected = receipt.get("encryptedArchiveSha256") if isinstance(receipt, dict) else None
    if not isinstance(expected, str) or expected != _hash_file(archive):
        raise RuntimeError("macmini_restore_encrypted_archive_hash_mismatch")


def _safe_extract(source: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in source.getmembers():
        destination = (target / member.name).resolve()
        if root != destination and root not in destination.parents:
            raise RuntimeError("macmini_restore_archive_path_escape")
        if member.issym() or member.islnk() or member.isdev():
            raise RuntimeError("macmini_restore_archive_special_file")
    source.extractall(target, filter="data")


def _verify_manifest(payload: Path) -> None:
    manifest = json.loads((payload / "SHA256MANIFEST.json").read_text(encoding="utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise RuntimeError("macmini_restore_manifest_invalid")
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError("macmini_restore_manifest_invalid")
        path = (payload / item["path"]).resolve()
        if payload.resolve() not in path.parents or not path.is_file():
            raise RuntimeError("macmini_restore_manifest_path_invalid")
        if item.get("size") != path.stat().st_size or item.get("sha256") != _hash_file(path):
            raise RuntimeError("macmini_restore_manifest_hash_mismatch")


def _ensure_namespace(args: argparse.Namespace) -> None:
    result = _kubectl(args, args.recovery_namespace, ("create", "namespace", args.recovery_namespace), 30)
    if result.returncode != 0 and b"AlreadyExists" not in result.stderr:
        raise RuntimeError("macmini_restore_namespace_create_failed")


def _copy_secret(args: argparse.Namespace, name: str) -> None:
    source = _kubectl(args, args.source_namespace, ("get", "secret", name, "-o", "json"), 30)
    payload = _json_command(source, "macmini_restore_secret_read_failed")
    if not isinstance(payload, dict):
        raise RuntimeError("macmini_restore_secret_invalid")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("macmini_restore_secret_invalid")
    payload["metadata"] = {"name": name, "namespace": args.recovery_namespace}
    applied = _kubectl_input(args, args.recovery_namespace, ("apply", "-f", "-"), json.dumps(payload).encode(), 30)
    if applied.returncode != 0:
        raise RuntimeError("macmini_restore_secret_apply_failed")


def _install_recovery(
    args: argparse.Namespace,
    temporary: Path,
    release_values: Path,
    release_chart: Path,
    *,
    is_foundation: bool,
) -> None:
    overrides = temporary / "recovery-overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "web": {"service": {"type": "ClusterIP", "nodePort": None}},
                "qaDependencies": {"keycloak": {"service": {"type": "ClusterIP", "nodePort": None}}},
            }
        ),
        encoding="utf-8",
    )
    os.chmod(overrides, 0o600)
    command = [
        args.helm,
        "upgrade",
        "--install",
        "foundry-lite",
        str(release_chart),
        "--namespace",
        args.recovery_namespace,
        "--values",
        str(release_values),
        "--values",
        str(overrides),
    ]
    if is_foundation:
        foundation = _foundation_values(temporary)
        command.extend(("--values", str(foundation)))
    command.append("--force-conflicts")
    command.extend(
        (
            "--atomic",
            "--wait",
            "--wait-for-jobs",
            "--timeout",
            "15m",
        )
    )
    try:
        result = subprocess.run(  # nosec B603 - validated Helm argv; remove if shell or free argv appears.
            command, check=False, capture_output=True, timeout=1000
        )
    except subprocess.TimeoutExpired as exc:
        phase = "foundation" if is_foundation else "full"
        raise RuntimeError(f"macmini_restore_{phase}_helm_timeout") from exc
    if result.returncode != 0:
        raise RuntimeError(_helm_install_failure_reason(result, is_foundation=is_foundation))


def _helm_install_failure_reason(result: subprocess.CompletedProcess[bytes], *, is_foundation: bool) -> str:
    phase = "foundation" if is_foundation else "full"
    output = (result.stdout + result.stderr).lower()
    failure = "field_conflict" if b"conflict occurred while applying object" in output else "command_failed"
    return f"macmini_restore_{phase}_helm_{failure}"


def _archived_release_values(payload: Path) -> Path:
    path = payload / "helm-release-values.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_restore_helm_values_invalid") from exc
    _validated_release_values(value)
    return path


def _archived_release_chart(payload: Path) -> Path:
    path = payload / "helm-chart.tgz"
    if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError("macmini_restore_helm_chart_invalid")
    return path


def _foundation_values(temporary: Path) -> Path:
    path = temporary / "recovery-foundation.json"
    path.write_text(
        json.dumps(
            {
                "api": {"replicas": 0},
                "web": {"replicas": 0},
                "executionBroker": {"enabled": False},
                "releaseController": {"enabled": False},
                "migrations": {"enabled": False},
                "runtimePersistence": {"enabled": False},
                "secrets": {"bootstrapOauthSigningSecret": False},
                "workers": {name: {"enabled": False} for name in ("outbox", "scheduler", "pipeline", "action")},
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def _ensure_recovery_runtime_pvc(args: argparse.Namespace, release_values: Path) -> None:
    values = json.loads(release_values.read_text(encoding="utf-8"))
    storage_class, size = _runtime_pvc_coordinates(values)
    manifest = _runtime_pvc_manifest(args.recovery_namespace, storage_class, size)
    result = _kubectl_input(
        args,
        args.recovery_namespace,
        ("apply", "-f", "-"),
        json.dumps(manifest, separators=(",", ":")).encode(),
        30,
    )
    if result.returncode != 0:
        raise RuntimeError("macmini_restore_runtime_pvc_apply_failed")


def _runtime_pvc_coordinates(values: object) -> tuple[str, str]:
    root = _runtime_pvc_mapping(values)
    global_values = _runtime_pvc_mapping(root.get("global"))
    runtime = _runtime_pvc_mapping(root.get("runtimePersistence"))
    if runtime.get("enabled") is not True:
        raise RuntimeError("macmini_restore_runtime_pvc_values_invalid")
    storage_class = _runtime_pvc_value(global_values.get("storageClass"), _KUBERNETES_NAME)
    size = _runtime_pvc_value(runtime.get("size"), _STORAGE_SIZE)
    return storage_class, size


def _runtime_pvc_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("macmini_restore_runtime_pvc_values_invalid")
    return value


def _runtime_pvc_value(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RuntimeError("macmini_restore_runtime_pvc_values_invalid")
    return value


def _runtime_pvc_manifest(namespace: str, storage_class: str, size: str) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": "foundry-lite-runtime",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "foundry-lite",
                "app.kubernetes.io/instance": "foundry-lite",
                "app.kubernetes.io/managed-by": "Helm",
            },
            "annotations": {
                "meta.helm.sh/release-name": "foundry-lite",
                "meta.helm.sh/release-namespace": namespace,
                "helm.sh/resource-policy": "keep",
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": storage_class,
            "resources": {"requests": {"storage": size}},
        },
    }


def _scale_named(args: argparse.Namespace, namespace: str, name: str, replicas: int) -> None:
    _scale_workload(args, namespace, "deployment", name, replicas)


def _scale_workload(args: argparse.Namespace, namespace: str, kind: str, name: str, replicas: int) -> None:
    operation = _replica_patch(kind, name, replicas)
    result = _kubectl(args, namespace, operation, 30)
    if result.returncode != 0:
        raise RuntimeError("macmini_restore_scale_failed")


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


def _workload_replicas(args: argparse.Namespace, namespace: str, kind: str, name: str) -> int:
    operation = ("get", kind, name, "-o", "jsonpath={.spec.replicas}")
    result = _kubectl(args, namespace, operation, 30)
    try:
        replicas = int(result.stdout.decode().strip())
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("macmini_restore_replica_inventory_failed") from exc
    if result.returncode != 0 or replicas < 0 or replicas > 10:
        raise RuntimeError("macmini_restore_replica_inventory_failed")
    return replicas


def _handoff_source_capacity(args: argparse.Namespace) -> list[CapacityReceipt]:
    receipts: list[CapacityReceipt] = [
        CapacityReceipt(
            kind=kind,
            name=name,
            replicasBefore=_workload_replicas(args, args.source_namespace, kind, name),
            replicasDuringRecovery=target,
        )
        for kind, name, target in _SOURCE_CAPACITY_HANDOFF
    ]
    for item in receipts:
        _scale_workload(
            args,
            args.source_namespace,
            item["kind"],
            item["name"],
            item["replicasDuringRecovery"],
        )
    return receipts


def _restore_source_capacity(args: argparse.Namespace, receipts: list[CapacityReceipt]) -> None:
    for item in receipts:
        _scale_workload(
            args,
            args.source_namespace,
            item["kind"],
            item["name"],
            item["replicasBefore"],
        )
    for item in receipts:
        if item["replicasBefore"] > 0:
            _wait_workload(args, args.source_namespace, item["kind"], item["name"], 300)


def _hibernate_recovery(args: argparse.Namespace) -> None:
    workloads = _existing_recovery_workloads(args)
    for kind, name in workloads:
        _scale_workload(args, args.recovery_namespace, kind, name, 0)
    for kind, name in workloads:
        _wait_workload(args, args.recovery_namespace, kind, name, 300)


def _existing_recovery_workloads(args: argparse.Namespace) -> tuple[tuple[str, str], ...]:
    operation = ("get", "deployment,statefulset", "-o", "json")
    payload = _json_command(
        _kubectl(args, args.recovery_namespace, operation, 60),
        "macmini_restore_recovery_inventory_failed",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RuntimeError("macmini_restore_recovery_inventory_failed")
    existing = {
        (str(item.get("kind", "")).lower(), str(item.get("metadata", {}).get("name", "")))
        for item in payload["items"]
        if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
    }
    return tuple(item for item in _RECOVERY_HIBERNATE if item in existing)


def _best_effort_capacity_recovery(args: argparse.Namespace, receipts: list[CapacityReceipt]) -> None:
    try:
        _hibernate_recovery(args)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    try:
        _restore_source_capacity(args, receipts)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass


def _restore_postgresql(args: argparse.Namespace, dump: Path) -> None:
    operation = (
        "exec",
        "-i",
        "statefulset/foundry-lite-postgresql",
        "--",
        "pg_restore",
        "-U",
        "postgres",
        "-d",
        "foundry_lite",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
    )
    with dump.open("rb") as stream:
        result = subprocess.run(  # nosec B603 - recovery-bound kubectl argv; remove if free argv appears.
            _kubectl_argv(args, args.recovery_namespace, operation),
            stdin=stream,
            check=False,
            capture_output=True,
            timeout=1800,
        )
    if result.returncode != 0:
        raise RuntimeError("macmini_restore_pg_restore_failed")


def _restore_s3(args: argparse.Namespace, archive: Path) -> S3RestoreReceipt:
    purged = 0
    reason = "macmini_restore_s3_import_command_failed"
    for attempt in range(1, _S3_IMPORT_ATTEMPTS + 1):
        purged += _purge_recovery_s3(args)
        try:
            result = _import_s3_archive(args, archive)
        except subprocess.TimeoutExpired:
            reason = "macmini_restore_s3_import_timeout"
            continue
        if result.returncode == 0:
            return S3RestoreReceipt(attempts=attempt, purgedVersionCount=purged)
        reason = _s3_import_failure_reason(result)
    raise RuntimeError(reason)


def _purge_recovery_s3(args: argparse.Namespace) -> int:
    result = _kubectl(args, args.recovery_namespace, _s3_snapshot_operation("purge", is_streaming=False), 300)
    payload = _json_command(result, "macmini_restore_s3_purge_failed")
    if not isinstance(payload, dict):
        raise RuntimeError("macmini_restore_s3_purge_failed")
    count = payload.get("deletedVersionCount")
    if payload.get("status") != "passed" or not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise RuntimeError("macmini_restore_s3_purge_failed")
    return count


def _import_s3_archive(args: argparse.Namespace, archive: Path) -> subprocess.CompletedProcess[bytes]:
    operation = _s3_snapshot_operation("import", is_streaming=True)
    with archive.open("rb") as stream:
        return subprocess.run(  # nosec B603 - recovery-bound kubectl argv; remove if free argv appears.
            _kubectl_argv(args, args.recovery_namespace, operation),
            stdin=stream,
            check=False,
            capture_output=True,
            timeout=3600,
        )


def _s3_snapshot_operation(mode: str, *, is_streaming: bool) -> tuple[str, ...]:
    operation = (
        "exec",
        *(("-i",) if is_streaming else ()),
        "deployment/foundry-lite",
        "-c",
        "api",
        "--",
        "/opt/foundry-lite-venv/bin/python",
        "/app/scripts/operations/s3_version_snapshot.py",
        mode,
    )
    return operation


def _s3_import_failure_reason(result: subprocess.CompletedProcess[bytes]) -> str:
    output = (result.stdout + result.stderr).lower()
    if match := re.search(rb"s3_snapshot_[a-z0-9_]{1,100}", output):
        return "macmini_restore_" + match.group().decode()
    transport_markers = (b"unexpected eof", b"lost connection", b"error executing command", b"broken pipe")
    if any(marker in output for marker in transport_markers):
        return "macmini_restore_s3_import_transport_failed"
    return "macmini_restore_s3_import_command_failed"


def _postgres_inventory(args: argparse.Namespace, namespace: str) -> object:
    query = (
        "SELECT json_build_object("
        "'schemaRevision',(SELECT version_num FROM alembic_version LIMIT 1),"
        "'auditEvents',(SELECT count(*) FROM audit_events),"
        "'outboxEvents',(SELECT count(*) FROM outbox_events),"
        "'actionRuns',(SELECT count(*) FROM action_runs),"
        "'datasetVersions',(SELECT count(*) FROM dataset_versions));"
    )
    operation = (
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
    return _json_command(_kubectl(args, namespace, operation, 60), "macmini_restore_inventory_failed")


def _wait_all_recovery(args: argparse.Namespace) -> None:
    for name, replicas in _APP_DEPLOYMENTS:
        if replicas > 0:
            _wait_deployment(args, args.recovery_namespace, name, 300)


def _wait_deployment(args: argparse.Namespace, namespace: str, name: str, timeout: int) -> None:
    _wait_workload(args, namespace, "deployment", name, timeout)


def _wait_workload(args: argparse.Namespace, namespace: str, kind: str, name: str, timeout: int) -> None:
    operation = ("rollout", "status", f"{kind}/{name}", f"--timeout={timeout}s")
    result = _kubectl(args, namespace, operation, timeout + 20)
    if result.returncode != 0:
        raise RuntimeError("macmini_restore_rollout_failed")


def _resume_source_workers(args: argparse.Namespace, payload: Path) -> None:
    values = json.loads((payload / "paused-workers.json").read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise RuntimeError("macmini_restore_worker_receipt_invalid")
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RuntimeError("macmini_restore_worker_receipt_invalid")
        replicas = item.get("replicasBefore")
        if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 0 or replicas > 10:
            raise RuntimeError("macmini_restore_worker_receipt_invalid")
        _scale_named(args, args.source_namespace, item["name"], replicas)
        if replicas > 0:
            _wait_deployment(args, args.source_namespace, item["name"], 300)


def _best_effort_source_worker_recovery(args: argparse.Namespace, payload: Path) -> None:
    try:
        _resume_source_workers(args, payload)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass


@contextmanager
def _port_forward(args: argparse.Namespace, namespace: str, local_port: int) -> Iterator[None]:
    operation = ("port-forward", "service/foundry-lite-api", f"{local_port}:10000")
    process = subprocess.Popen(  # nosec B603 - fixed port-forward argv; remove if shell or free argv appears.
        _kubectl_argv(args, namespace, operation),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_http(f"http://127.0.0.1:{local_port}/healthz", process)
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_http(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("macmini_restore_port_forward_failed")
        try:
            with urllib.request.urlopen(  # nosec B310 - fixed loopback HTTP probe; remove if remote URLs are accepted.
                url, timeout=2
            ) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    raise RuntimeError("macmini_restore_port_forward_timeout")


def _api_post(base: str, token: str, path: str, payload: object) -> dict[str, object]:
    parsed_base = urllib.parse.urlsplit(base)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
        raise ValueError("macmini_restore_api_url_invalid")
    if parsed_base.scheme == "http" and parsed_base.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("macmini_restore_api_http_not_loopback")
    url = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    if urllib.parse.urlsplit(url).netloc != parsed_base.netloc:
        raise ValueError("macmini_restore_api_origin_mismatch")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        method="POST",
        headers=_operator_api_headers(token),
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=_API_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_API_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("macmini_restore_api_unavailable") from exc
    if len(body) > _MAX_API_BYTES:
        raise RuntimeError("macmini_restore_api_response_too_large")
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_restore_api_response_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("macmini_restore_api_response_invalid")
    return value


def _api_get(base: str, token: str, path: str) -> dict[str, object]:
    parsed_base = urllib.parse.urlsplit(base)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
        raise ValueError("macmini_restore_api_url_invalid")
    if parsed_base.scheme == "http" and parsed_base.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("macmini_restore_api_http_not_loopback")
    url = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    if urllib.parse.urlsplit(url).netloc != parsed_base.netloc:
        raise ValueError("macmini_restore_api_origin_mismatch")
    request = urllib.request.Request(url, method="GET", headers=_operator_api_headers(token))
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=_API_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_API_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("macmini_restore_api_unavailable") from exc
    if len(body) > _MAX_API_BYTES:
        raise RuntimeError("macmini_restore_api_response_too_large")
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_restore_api_response_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("macmini_restore_api_response_invalid")
    return value


def _api_post_exact_retry(base: str, token: str, path: str, payload: object) -> dict[str, object]:
    for attempt in range(_API_REQUEST_ATTEMPTS):
        try:
            return _api_post(base, token, path, payload)
        except RuntimeError as exc:
            if str(exc) != "macmini_restore_api_unavailable" or attempt + 1 >= _API_REQUEST_ATTEMPTS:
                raise
            time.sleep(2)
    raise RuntimeError("macmini_restore_api_unavailable")


def _validate_and_approve_resume(
    base: str,
    token: str,
    restore_id: str,
    validation_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    root = f"/api/operations/backup-restore/restore-mode/{restore_id}"
    payload = {"validationId": validation_id}
    validation = _api_post_exact_retry(base, token, f"{root}/post-restore-validation", payload)
    if validation.get("status") != "passed" or validation.get("validationId") != validation_id:
        raise RuntimeError("macmini_restore_post_validation_failed")
    try:
        approval = _api_post_exact_retry(base, token, f"{root}/approve-resume", payload)
    except RuntimeError as exc:
        if str(exc) != "macmini_restore_api_unavailable":
            raise
        approval = _api_get(base, token, root)
    if approval.get("status") != "resume_approved":
        raise RuntimeError("macmini_restore_resume_approval_failed")
    return validation, approval


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, fp: object, code: int, msg: str, headers: object, url: str) -> None:
        raise RuntimeError("macmini_restore_api_redirect_not_allowed")


def _kubectl(
    args: argparse.Namespace,
    namespace: str,
    operation: tuple[str, ...],
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        _kubectl_argv(args, namespace, operation), check=False, capture_output=True, timeout=timeout
    )


def _kubectl_input(
    args: argparse.Namespace,
    namespace: str,
    operation: tuple[str, ...],
    payload: bytes,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        _kubectl_argv(args, namespace, operation),
        input=payload,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def _kubectl_argv(args: argparse.Namespace, namespace: str, operation: tuple[str, ...]) -> tuple[str, ...]:
    return (args.kubectl, "--kubeconfig", args.kubeconfig, "--namespace", namespace, *operation)


def _json_command(result: subprocess.CompletedProcess[bytes], reason: str) -> object:
    if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
        raise RuntimeError(reason)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(reason) from exc


def _private_path(path: Path) -> Path:
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("macmini_restore_private_file_invalid")
    return path


def _private_text(path: Path) -> str:
    value = _private_path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("macmini_restore_private_file_empty")
    return value


def _backup_archive(run_id: str) -> Path:
    if not run_id or not all(value.isalnum() or value in "-_" for value in run_id):
        raise ValueError("macmini_restore_run_id_invalid")
    path = QA_ROOT / "backups" / f"{run_id}.tar.age"
    if not path.is_file():
        raise ValueError("macmini_restore_archive_missing")
    return path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_receipt(run_id: str, payload: object) -> None:
    target = QA_ROOT / "evidence" / run_id / "restore"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = target / "recovery-receipt.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _safe_failure_reason(exc: Exception) -> str:
    reason = str(exc)
    if re.fullmatch(r"[a-z0-9_.:-]{1,160}", reason):
        return reason
    return "macmini_restore_failed_unclassified"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-namespace", default="foundry-qa")
    parser.add_argument("--recovery-namespace", default=_RECOVERY_NAMESPACE)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--helm", default=str(QA_ROOT / "bin" / "helm"))
    parser.add_argument("--age", default=str(QA_ROOT / "bin" / "age"))
    parser.add_argument("--age-identity-file", required=True)
    parser.add_argument("--bearer-token-file", required=True)
    parser.add_argument("--source-api-base-url", default="http://127.0.0.1:30443")
    args = parser.parse_args()
    try:
        receipt = restore(args)
    except Exception as exc:
        receipt = {
            "schemaVersion": 1,
            "status": "failed",
            "runId": args.run_id,
            "reason": _safe_failure_reason(exc),
            "rawSecretsStored": False,
        }
        _write_receipt(args.run_id, receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
