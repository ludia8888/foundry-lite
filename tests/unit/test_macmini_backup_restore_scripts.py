from __future__ import annotations

import argparse
import io
import json
import subprocess
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from scripts.operations import backup_macmini_qa as backup_subject
from scripts.operations import restore_macmini_qa as restore_subject
from scripts.operations.backup_macmini_qa import _worker_identity
from scripts.operations.restore_macmini_qa import _safe_extract


def _exact_release_values() -> dict[str, object]:
    images = {
        name: {
            "repository": repository,
            "digest": f"sha256:{index:064x}",
        }
        for index, (name, repository) in enumerate(backup_subject._IMAGE_REPOSITORIES.items(), start=1)
    }
    return {
        "global": {
            "revision": "a" * 40,
            "imagePullSecrets": ["foundry-lite-ghcr"],
        },
        "images": images,
        "auth": {"profile": "header-trust"},
        "qaDependencies": {"enabled": True},
    }


def test_backup_pauses_only_worker_component_deployments() -> None:
    worker = {
        "metadata": {"name": "foundry-lite-worker-action", "labels": {"app.kubernetes.io/component": "worker-action"}},
        "spec": {"replicas": 1},
    }
    api = {
        "metadata": {"name": "foundry-lite", "labels": {"app.kubernetes.io/component": "api"}},
        "spec": {"replicas": 2},
    }
    assert _worker_identity(worker) == ("foundry-lite-worker-action", 1)
    assert _worker_identity(api) is None


def test_backup_and_restore_scaling_preserve_helm_field_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    backup_commands: list[tuple[str, ...]] = []
    restore_commands: list[tuple[str, ...]] = []
    inventory = {
        "items": [
            {
                "metadata": {
                    "name": "foundry-lite-worker-action",
                    "labels": {"app.kubernetes.io/component": "worker-action"},
                },
                "spec": {"replicas": 1},
            }
        ]
    }

    def backup_kubectl(
        _args: argparse.Namespace, operation: tuple[str, ...], _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        backup_commands.append(operation)
        stdout = json.dumps(inventory).encode() if operation[0] == "get" else b""
        return subprocess.CompletedProcess(operation, 0, stdout, b"")

    def restore_kubectl(
        _args: argparse.Namespace,
        _namespace: str,
        operation: tuple[str, ...],
        _timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        restore_commands.append(operation)
        return subprocess.CompletedProcess(operation, 0, b"", b"")

    monkeypatch.setattr(backup_subject, "_kubectl", backup_kubectl)
    monkeypatch.setattr(restore_subject, "_kubectl", restore_kubectl)

    backup_subject._pause_workers(argparse.Namespace())
    restore_subject._scale_named(argparse.Namespace(), "foundry-qa-recovery", "foundry-lite", 1)

    assert "--subresource=scale" not in backup_commands[-1]
    assert "--field-manager=helm" in backup_commands[-1]
    assert "--subresource=scale" not in restore_commands[-1]
    assert "--field-manager=helm" in restore_commands[-1]


def test_backup_restore_operator_headers_cover_oidc_and_header_trust() -> None:
    headers = backup_subject._operator_api_headers("operator-token")

    assert headers["authorization"] == "Bearer operator-token"
    assert headers["X-Tenant-ID"] == "tenant-demo"
    assert headers["X-User-ID"] == "enterprise-qa-operator"
    assert headers["X-Roles"] == "admin,data_engineer,ops_manager"


def test_backup_confirms_committed_restore_mode_after_lost_start_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backup_subject,
        "_api_json",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("macmini_backup_api_unavailable")),
    )
    monkeypatch.setattr(
        backup_subject,
        "_api_get_json",
        lambda *_args: {"status": "paused", "backupId": "backup-1", "restoreId": "restore-1"},
    )

    status = backup_subject._start_restore_mode("http://127.0.0.1:30443", "token", "backup-1", "restore-1")

    assert status["status"] == "paused"
    assert status["backupId"] == "backup-1"


def test_backup_and_restore_allow_bounded_long_running_preflight() -> None:
    assert backup_subject._API_TIMEOUT_SECONDS == 600
    assert restore_subject._API_TIMEOUT_SECONDS == 600


def test_backup_exactly_retries_lost_mutation_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def api_json(*_args: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("macmini_backup_api_timeout")
        return {"status": "existing", "backupId": "backup-1"}

    monkeypatch.setattr(backup_subject, "_api_json", api_json)
    monkeypatch.setattr(backup_subject.time, "sleep", lambda _seconds: None)

    result = backup_subject._api_post_exact_retry(
        "http://127.0.0.1:30443",
        "token",
        "/api/operations/backup-restore/artifacts",
        {"backupId": "backup-1"},
    )

    assert result["status"] == "existing"
    assert calls == 2


def test_restore_exactly_retries_lost_validation_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def api_post(_base: str, _token: str, path: str, payload: object) -> dict[str, object]:
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("macmini_restore_api_unavailable")
        if path.endswith("post-restore-validation"):
            return {"status": "passed", "validationId": payload["validationId"]}  # type: ignore[index]
        return {"status": "resume_approved"}

    monkeypatch.setattr(restore_subject, "_api_post", api_post)
    monkeypatch.setattr(restore_subject.time, "sleep", lambda _seconds: None)

    validation, approval = restore_subject._validate_and_approve_resume(
        "http://127.0.0.1:18082",
        "token",
        "restore-1",
        "validation-1",
    )

    assert validation["status"] == "passed"
    assert approval["status"] == "resume_approved"
    assert calls.count("/api/operations/backup-restore/restore-mode/restore-1/post-restore-validation") == 2


def test_restore_confirms_committed_approval_after_lost_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    def api_post(_base: str, _token: str, path: str, payload: object) -> dict[str, object]:
        if path.endswith("post-restore-validation"):
            return {"status": "passed", "validationId": payload["validationId"]}  # type: ignore[index]
        raise RuntimeError("macmini_restore_api_unavailable")

    monkeypatch.setattr(restore_subject, "_api_post", api_post)
    monkeypatch.setattr(restore_subject, "_api_get", lambda *_args: {"status": "resume_approved"})
    monkeypatch.setattr(restore_subject.time, "sleep", lambda _seconds: None)

    _validation, approval = restore_subject._validate_and_approve_resume(
        "http://127.0.0.1:18082",
        "token",
        "restore-1",
        "validation-1",
    )

    assert approval["status"] == "resume_approved"


def test_restore_rejects_unapproved_resume_before_worker_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        restore_subject,
        "_api_post",
        lambda _base, _token, path, payload: (
            {"status": "passed", "validationId": payload["validationId"]}
            if path.endswith("post-restore-validation")
            else {"status": "paused"}
        ),
    )

    with pytest.raises(RuntimeError, match="resume_approval_failed"):
        restore_subject._validate_and_approve_resume(
            "http://127.0.0.1:18082",
            "token",
            "restore-1",
            "validation-1",
        )


def test_single_node_restore_hands_capacity_back_to_source(monkeypatch: pytest.MonkeyPatch) -> None:
    scaled: list[tuple[str, str, str, int]] = []
    waited: list[tuple[str, str, str]] = []

    def replicas(_args: argparse.Namespace, _namespace: str, kind: str, name: str) -> int:
        return 2 if kind == "deployment" and name in {"foundry-lite", "foundry-lite-web"} else 1

    def scale(
        _args: argparse.Namespace,
        namespace: str,
        kind: str,
        name: str,
        count: int,
    ) -> None:
        scaled.append((namespace, kind, name, count))

    def wait(
        _args: argparse.Namespace,
        namespace: str,
        kind: str,
        name: str,
        _timeout: int,
    ) -> None:
        waited.append((namespace, kind, name))

    monkeypatch.setattr(restore_subject, "_workload_replicas", replicas)
    monkeypatch.setattr(restore_subject, "_scale_workload", scale)
    monkeypatch.setattr(restore_subject, "_wait_workload", wait)
    monkeypatch.setattr(
        restore_subject,
        "_existing_recovery_workloads",
        lambda _args: restore_subject._RECOVERY_HIBERNATE,
    )
    args = argparse.Namespace(source_namespace="foundry-qa", recovery_namespace="foundry-qa-recovery")

    receipt = restore_subject._handoff_source_capacity(args)
    restore_subject._hibernate_recovery(args)
    restore_subject._restore_source_capacity(args, receipt)

    assert ("foundry-qa", "deployment", "foundry-lite", 1) in scaled
    assert ("foundry-qa-recovery", "statefulset", "foundry-lite-postgresql", 0) in scaled
    assert ("foundry-qa-recovery", "deployment", "foundry-lite-worker-action", 0) in scaled
    assert ("foundry-qa", "deployment", "foundry-lite", 2) in scaled
    assert ("foundry-qa", "statefulset", "foundry-lite-keycloak") in waited


def test_restore_hibernates_only_workloads_present_in_foundation_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scaled: list[tuple[str, str, str, int]] = []
    waited: list[tuple[str, str, str]] = []
    present = (
        ("deployment", "foundry-lite"),
        ("deployment", "foundry-lite-temporal"),
        ("statefulset", "foundry-lite-postgresql"),
    )
    monkeypatch.setattr(restore_subject, "_existing_recovery_workloads", lambda _args: present)
    monkeypatch.setattr(
        restore_subject,
        "_scale_workload",
        lambda _args, namespace, kind, name, count: scaled.append((namespace, kind, name, count)),
    )
    monkeypatch.setattr(
        restore_subject,
        "_wait_workload",
        lambda _args, namespace, kind, name, _timeout: waited.append((namespace, kind, name)),
    )

    restore_subject._hibernate_recovery(argparse.Namespace(recovery_namespace="foundry-qa-recovery"))

    assert scaled == [("foundry-qa-recovery", kind, name, 0) for kind, name in present]
    assert waited == [("foundry-qa-recovery", kind, name) for kind, name in present]


def test_restore_inventory_excludes_absent_and_unknown_recovery_workloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = {
        "items": [
            {"kind": "Deployment", "metadata": {"name": "foundry-lite"}},
            {"kind": "StatefulSet", "metadata": {"name": "foundry-lite-postgresql"}},
            {"kind": "Deployment", "metadata": {"name": "unrelated-workload"}},
        ]
    }
    monkeypatch.setattr(
        restore_subject,
        "_kubectl",
        lambda *_args: subprocess.CompletedProcess((), 0, json.dumps(inventory).encode(), b""),
    )
    args = argparse.Namespace(recovery_namespace="foundry-qa-recovery")

    workloads = restore_subject._existing_recovery_workloads(args)

    assert workloads == (
        ("deployment", "foundry-lite"),
        ("statefulset", "foundry-lite-postgresql"),
    )


def test_restore_retries_partial_s3_import_from_clean_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "s3-versions.tar"
    archive.write_bytes(b"streamed-snapshot")
    purged = iter((0, 9))
    stream_positions: list[int] = []

    monkeypatch.setattr(restore_subject, "_purge_recovery_s3", lambda _args: next(purged))

    def run(_command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stream = cast(BinaryIO, kwargs["stdin"])
        stream_positions.append(stream.tell())
        stream.read()
        if len(stream_positions) == 1:
            return subprocess.CompletedProcess(
                _command,
                1,
                b"",
                b"error: Internal error occurred: error executing command in container: unexpected EOF",
            )
        return subprocess.CompletedProcess(_command, 0, b"", b"")

    monkeypatch.setattr(restore_subject.subprocess, "run", run)
    args = argparse.Namespace(
        recovery_namespace="foundry-qa-recovery",
        kubectl="kubectl",
        kubeconfig="/qa/kubeconfig",
    )

    receipt = restore_subject._restore_s3(args, archive)

    assert receipt == {"attempts": 2, "purgedVersionCount": 9}
    assert stream_positions == [0, 0]


def test_restore_classifies_minio_missing_content_md5() -> None:
    result = subprocess.CompletedProcess(
        (),
        1,
        b"",
        b"ClientError: MissingContentMD5: Missing required header: Content-Md5",
    )

    assert restore_subject._s3_purge_failure_reason(result) == "macmini_restore_s3_purge_content_md5_missing"


def test_failed_restore_approves_source_before_worker_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    calls: list[str] = []

    @contextmanager
    def port_forward(*_args: object) -> Iterator[None]:
        calls.append("port-forward-enter")
        yield
        calls.append("port-forward-exit")

    def approve(*_args: object) -> tuple[dict[str, str], dict[str, str]]:
        calls.append("approved")
        return {"status": "passed"}, {"status": "resume_approved"}

    monkeypatch.setattr(restore_subject, "_port_forward", port_forward)
    monkeypatch.setattr(restore_subject, "_validate_and_approve_resume", approve)

    is_approved = restore_subject._best_effort_failed_restore_source_approval(
        argparse.Namespace(run_id="run-1", source_namespace="foundry-qa"),
        "token",
    )

    assert is_approved is True
    assert calls == ["port-forward-enter", "approved", "port-forward-exit"]

    def confirm_approval(*_args: object) -> bool:
        calls.append("approval-confirmed")
        return True

    monkeypatch.setattr(restore_subject, "_best_effort_failed_restore_source_approval", confirm_approval)
    monkeypatch.setattr(
        restore_subject,
        "_best_effort_source_worker_recovery",
        lambda *_args: calls.append("workers-recovered"),
    )
    restore_subject._best_effort_failed_restore_source_recovery(
        argparse.Namespace(),
        "token",
        payload,
        is_source_resume_approved=False,
    )

    assert calls[-2:] == ["approval-confirmed", "workers-recovered"]

    calls.clear()

    def reject_approval(*_args: object) -> bool:
        calls.append("approval-rejected")
        return False

    monkeypatch.setattr(restore_subject, "_best_effort_failed_restore_source_approval", reject_approval)
    restore_subject._best_effort_failed_restore_source_recovery(
        argparse.Namespace(),
        "token",
        payload,
        is_source_resume_approved=False,
    )

    assert calls == ["approval-rejected"]


def test_restore_waits_for_each_resumed_source_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "paused-workers.json").write_text(
        json.dumps(
            [
                {"name": "foundry-lite-worker-action", "replicasBefore": 1},
                {"name": "foundry-lite-worker-outbox", "replicasBefore": 0},
            ]
        ),
        encoding="utf-8",
    )
    scaled: list[tuple[str, str, int]] = []
    waited: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        restore_subject,
        "_scale_named",
        lambda _args, namespace, name, replicas: scaled.append((namespace, name, replicas)),
    )
    monkeypatch.setattr(
        restore_subject,
        "_wait_deployment",
        lambda _args, namespace, name, timeout: waited.append((namespace, name, timeout)),
    )

    restore_subject._resume_source_workers(argparse.Namespace(source_namespace="foundry-qa"), payload)

    assert scaled == [
        ("foundry-qa", "foundry-lite-worker-action", 1),
        ("foundry-qa", "foundry-lite-worker-outbox", 0),
    ]
    assert waited == [("foundry-qa", "foundry-lite-worker-action", 300)]


def test_backup_freezes_commit_point_after_platform_evidence_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    target = tmp_path / "backup"
    target.mkdir()

    def api_json(_base: str, _token: str, path: str, _payload: object) -> dict[str, object]:
        events.append(path)
        if path.endswith("/start"):
            return {"status": "paused"}
        return {
            "preflightStatus": "ready",
            "artifactRef": "/runtime/backups/run-1.json",
            "artifactHash": "sha256:" + "a" * 64,
            "datasetVersionCount": 30_000,
            "issueCount": 0,
            "payloadByteSize": 4_000_000,
            "backupId": "run-1",
        }

    class CommitPointCaptured(RuntimeError):
        pass

    def postgres_inventory(_args: argparse.Namespace) -> object:
        events.append("postgres-inventory")
        raise CommitPointCaptured

    monkeypatch.setattr(backup_subject, "_api_json", api_json)
    monkeypatch.setattr(backup_subject, "_pause_workers", lambda _args, **_kwargs: [])
    monkeypatch.setattr(backup_subject, "_helm_release_values", lambda _args: _exact_release_values())
    monkeypatch.setattr(backup_subject, "_package_release_chart", lambda *_args: target / "helm-chart.tgz")
    monkeypatch.setattr(backup_subject, "_postgres_inventory", postgres_inventory)
    args = argparse.Namespace(
        run_id="run-1",
        api_base_url="http://127.0.0.1:30443",
    )
    progress = backup_subject.BackupProgress(isRestoreModeActive=False, workers=[])

    with pytest.raises(CommitPointCaptured):
        backup_subject._capture_backup(args, "operator-token", "age1recipient", target, "restore-1", progress)

    assert events == [
        "/api/operations/backup-restore/restore-mode/start",
        "/api/operations/backup-restore/artifacts",
        "postgres-inventory",
    ]


def test_backup_writes_only_bounded_preflight_summary_from_immutable_artifact() -> None:
    artifact = {
        "preflightStatus": "ready",
        "artifactRef": "/runtime/backups/large.json",
        "artifactHash": "sha256:" + "b" * 64,
        "datasetVersionCount": 30_575,
        "issueCount": 0,
        "payloadByteSize": 8_000_000,
        "backupId": "large-backup",
        "datasetVersions": [{"versionId": str(index)} for index in range(100)],
        "summary": {"unbounded": [str(index) for index in range(100)]},
    }

    summary = backup_subject._artifact_preflight_summary(artifact, expected_backup_id="large-backup")

    assert summary["datasetVersionCount"] == 30_575
    assert summary["detailsStoredInImmutableArtifact"] is True
    assert "datasetVersions" not in summary
    assert "summary" not in summary
    assert len(json.dumps(summary)) < 1024

    with pytest.raises(RuntimeError, match="artifact_receipt_invalid"):
        backup_subject._artifact_preflight_summary(artifact, expected_backup_id="other-backup")


def test_backup_extracts_large_s3_manifest_from_streamed_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "s3-versions.tar"
    manifest_path = tmp_path / "s3-manifest.json"
    manifest = {
        "schemaVersion": 1,
        "bucket": "foundry-lite",
        "entries": [
            {
                "key": f"datasets/tenant-demo/ops.action_log/versions/dsv_{index:032x}/data/part-00000.parquet",
                "versionId": f"version-{index:032x}",
                "isDeleteMarker": False,
                "size": 4096,
                "lastModified": "2026-08-23T11:03:26.000000+00:00",
                "sha256": "sha256:" + "a" * 64,
            }
            for index in range(62_158)
        ],
    }
    encoded = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    assert len(encoded) > 16 * 1024 * 1024
    with tarfile.open(archive_path, mode="w") as archive:
        payload = io.BytesIO(encoded)
        info = tarfile.TarInfo("manifest.json")
        info.size = len(encoded)
        archive.addfile(info, payload)

    backup_subject._s3_manifest_from_archive(archive_path, manifest_path)

    assert manifest_path.read_bytes() == encoded
    assert manifest_path.stat().st_mode & 0o077 == 0
    assert len(json.loads(manifest_path.read_bytes())["entries"]) == 62_158


def test_failed_backup_approves_resume_restores_exact_workers_and_removes_partial_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_paths: list[str] = []
    kubectl_operations: list[tuple[str, ...]] = []

    def api_json(_base: str, _token: str, path: str, _payload: object) -> dict[str, object]:
        api_paths.append(path)
        return {"status": "passed" if path.endswith("post-restore-validation") else "resume_approved"}

    def kubectl(
        _args: argparse.Namespace,
        operation: tuple[str, ...],
        _timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        kubectl_operations.append(operation)
        return subprocess.CompletedProcess(operation, 0, b"", b"")

    monkeypatch.setattr(backup_subject, "_api_json", api_json)
    monkeypatch.setattr(backup_subject, "_kubectl", kubectl)
    target = tmp_path / "run-1"
    target.mkdir()
    (target / "partial").write_text("partial", encoding="utf-8")
    (tmp_path / "run-1.tar").write_bytes(b"partial")
    (tmp_path / "run-1.tar.age").write_bytes(b"partial")
    args = argparse.Namespace(run_id="run-1", api_base_url="http://127.0.0.1:30443")
    progress = backup_subject.BackupProgress(
        isRestoreModeActive=True,
        workers=[
            {"name": "foundry-lite-worker-action", "replicasBefore": 1, "replicasAfter": 0},
            {"name": "foundry-lite-worker-outbox", "replicasBefore": 0, "replicasAfter": 0},
        ],
    )

    cleanup = backup_subject._recover_failed_backup(args, "token", target, "restore-1", progress)

    assert cleanup["status"] == "passed"
    assert cleanup["restoreModeStatus"] == "resume_approved"
    assert cleanup["workersRestored"] is True
    assert not target.exists()
    assert not (tmp_path / "run-1.tar").exists()
    assert not (tmp_path / "run-1.tar.age").exists()
    assert api_paths == [
        "/api/operations/backup-restore/restore-mode/restore-1/post-restore-validation",
        "/api/operations/backup-restore/restore-mode/restore-1/approve-resume",
    ]
    patch_operations = [operation for operation in kubectl_operations if operation[0] == "patch"]
    assert all("--field-manager=helm" in operation for operation in patch_operations)
    assert any(any('"replicas":1' in value for value in operation) for operation in patch_operations)
    assert any(any('"replicas":0' in value for value in operation) for operation in patch_operations)
    assert sum(operation[0] == "rollout" for operation in kubectl_operations) == 1


def test_failed_backup_does_not_restart_workers_before_resume_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_subject, "_api_json", lambda *_args: {"status": "blocked"})
    kubectl_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        backup_subject,
        "_kubectl",
        lambda _args, operation, _timeout: kubectl_calls.append(operation),
    )
    target = tmp_path / "run-2"
    target.mkdir()
    progress = backup_subject.BackupProgress(
        isRestoreModeActive=True,
        workers=[{"name": "foundry-lite-worker-action", "replicasBefore": 1}],
    )

    cleanup = backup_subject._recover_failed_backup(
        argparse.Namespace(run_id="run-2", api_base_url="http://127.0.0.1:30443"),
        "token",
        target,
        "restore-2",
        progress,
    )

    assert cleanup["status"] == "failed"
    assert cleanup["workersRestored"] is False
    assert kubectl_calls == []
    assert target.exists()
    assert cleanup["partialFilesRemoved"] is False


def test_worker_pause_failure_preserves_receipts_for_automatic_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = {
        "items": [
            {
                "metadata": {
                    "name": f"foundry-lite-worker-{name}",
                    "labels": {"app.kubernetes.io/component": f"worker-{name}"},
                },
                "spec": {"replicas": 1},
            }
            for name in ("action", "outbox")
        ]
    }
    calls = 0

    def kubectl(
        _args: argparse.Namespace,
        operation: tuple[str, ...],
        _timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        if operation[0] == "get":
            return subprocess.CompletedProcess(operation, 0, json.dumps(inventory).encode(), b"")
        calls += 1
        return subprocess.CompletedProcess(operation, 0 if calls == 1 else 1, b"", b"")

    monkeypatch.setattr(backup_subject, "_kubectl", kubectl)
    receipts: list[dict[str, object]] = []
    receipt_path = tmp_path / "paused-workers.json"
    receipt_path.with_name(f".{receipt_path.name}.tmp").write_text("interrupted", encoding="utf-8")

    with pytest.raises(RuntimeError, match="worker_pause_failed"):
        backup_subject._pause_workers(
            argparse.Namespace(),
            receipts=receipts,
            receipt_path=receipt_path,
        )

    assert receipts == [{"name": "foundry-lite-worker-action", "replicasBefore": 1, "replicasAfter": 0}]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipts


def test_restore_safe_extract_rejects_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "payload.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        info = tarfile.TarInfo("unsafe-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with tarfile.open(archive_path, mode="r") as archive:
        with pytest.raises(RuntimeError, match="special_file"):
            _safe_extract(archive, tmp_path / "target")


def test_restore_safe_extract_accepts_regular_bounded_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "payload.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        content = b"receipt"
        info = tarfile.TarInfo("run/database-inventory.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    target = tmp_path / "target"
    target.mkdir()
    with tarfile.open(archive_path, mode="r") as archive:
        _safe_extract(archive, target)
    assert (target / "run" / "database-inventory.json").read_bytes() == b"receipt"


def test_restore_verifies_encrypted_archive_before_decryption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    backups = qa_root / "backups"
    backups.mkdir(parents=True)
    archive = backups / "run-1.tar.age"
    archive.write_bytes(b"encrypted-backup")
    expected = restore_subject._hash_file(archive)
    receipt = backups / "run-1-backup-receipt.json"
    receipt.write_text(json.dumps({"encryptedArchiveSha256": expected}), encoding="utf-8")
    receipt.chmod(0o600)
    monkeypatch.setattr(restore_subject, "QA_ROOT", qa_root)

    restore_subject._verify_encrypted_archive("run-1", archive)
    archive.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="encrypted_archive_hash_mismatch"):
        restore_subject._verify_encrypted_archive("run-1", archive)


def test_restore_api_rejects_redirects() -> None:
    with pytest.raises(RuntimeError, match="redirect_not_allowed"):
        restore_subject._NoRedirect().redirect_request(
            object(), object(), 302, "redirect", object(), "https://evil.invalid"
        )


def test_backup_release_values_require_exact_images_auth_and_pull_secret() -> None:
    values = _exact_release_values()

    assert backup_subject._validated_release_values(values) == values

    values["images"]["api"]["digest"] = "latest"  # type: ignore[index]
    with pytest.raises(RuntimeError, match="helm_values_invalid"):
        backup_subject._validated_release_values(values)


def test_backup_packages_only_the_clean_exact_release_chart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_root = tmp_path / "foundry-qa"
    chart = qa_root / "repo" / "deploy" / "helm" / "foundry-lite"
    chart.mkdir(parents=True)
    target = qa_root / "backups" / "run-1"
    target.mkdir(parents=True)
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, b"a" * 40 + b"\n", b"")
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, b"", b"")
        (target / "foundry-lite-0.1.0.tgz").write_bytes(b"exact-chart-package")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(backup_subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(backup_subject.subprocess, "run", run)
    args = argparse.Namespace(git="git", helm="helm", chart=str(chart))

    packaged = backup_subject._package_release_chart(args, target, _exact_release_values())

    assert packaged == target / "helm-chart.tgz"
    assert packaged.read_bytes() == b"exact-chart-package"
    assert len(commands) == 3


def test_restore_copies_every_release_secret_needed_by_recovery() -> None:
    assert set(restore_subject._SECRETS) == {
        "foundry-lite-application",
        "foundry-lite-runtime-application",
        "foundry-lite-migration",
        "foundry-lite-oauth-signing",
        "foundry-lite-qa-dependencies",
        "foundry-lite-backup-age",
        "foundry-lite-ghcr",
    }


def test_restore_reads_and_validates_archived_release_values(tmp_path: Path) -> None:
    path = tmp_path / "helm-release-values.json"
    path.write_text(json.dumps(_exact_release_values()), encoding="utf-8")

    assert restore_subject._archived_release_values(tmp_path) == path

    path.write_text(json.dumps({"global": {"revision": "mutable"}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="helm_values_invalid"):
        restore_subject._archived_release_values(tmp_path)


def test_restore_requires_a_bounded_archived_release_chart(tmp_path: Path) -> None:
    chart = tmp_path / "helm-chart.tgz"
    chart.write_bytes(b"exact-chart-package")

    assert restore_subject._archived_release_chart(tmp_path) == chart

    chart.write_bytes(b"")
    with pytest.raises(RuntimeError, match="helm_chart_invalid"):
        restore_subject._archived_release_chart(tmp_path)


def test_restore_creates_helm_owned_runtime_pvc_before_temporary_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_values = _exact_release_values()
    release_values["global"]["storageClass"] = "local-path"  # type: ignore[index]
    release_values["runtimePersistence"] = {"enabled": True, "size": "5Gi"}
    path = tmp_path / "values.json"
    path.write_text(json.dumps(release_values), encoding="utf-8")
    captured: dict[str, object] = {}

    def kubectl_input(
        _args: argparse.Namespace,
        namespace: str,
        operation: tuple[str, ...],
        payload: bytes,
        _timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        captured.update({"namespace": namespace, "operation": operation, "manifest": json.loads(payload)})
        return subprocess.CompletedProcess(operation, 0, b"", b"")

    monkeypatch.setattr(restore_subject, "_kubectl_input", kubectl_input)
    args = argparse.Namespace(recovery_namespace="foundry-qa-recovery")

    restore_subject._ensure_recovery_runtime_pvc(args, path)

    manifest = captured["manifest"]
    assert captured["namespace"] == "foundry-qa-recovery"
    assert manifest["metadata"]["annotations"]["meta.helm.sh/release-name"] == "foundry-lite"  # type: ignore[index]
    assert manifest["spec"]["storageClassName"] == "local-path"  # type: ignore[index]


@pytest.mark.parametrize(
    "runtime,storage_class",
    [
        ({"enabled": False, "size": "5Gi"}, "local-path"),
        ({"enabled": True, "size": "unbounded"}, "local-path"),
        ({"enabled": True, "size": "5Gi"}, "../shared"),
    ],
)
def test_restore_rejects_unsafe_runtime_pvc_values(
    tmp_path: Path,
    runtime: dict[str, object],
    storage_class: str,
) -> None:
    release_values = _exact_release_values()
    release_values["global"]["storageClass"] = storage_class  # type: ignore[index]
    release_values["runtimePersistence"] = runtime
    path = tmp_path / "values.json"
    path.write_text(json.dumps(release_values), encoding="utf-8")

    with pytest.raises(RuntimeError, match="runtime_pvc_values_invalid"):
        restore_subject._ensure_recovery_runtime_pvc(
            argparse.Namespace(recovery_namespace="foundry-qa-recovery"),
            path,
        )


def test_restore_helm_install_uses_exact_values_in_two_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived = tmp_path / "helm-release-values.json"
    archived.write_text(json.dumps(_exact_release_values()), encoding="utf-8")
    chart = tmp_path / "helm-chart.tgz"
    chart.write_bytes(b"exact-chart-package")
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(restore_subject.subprocess, "run", run)
    args = argparse.Namespace(
        helm="/qa/bin/helm",
        recovery_namespace="foundry-qa-recovery",
    )

    restore_subject._install_recovery(args, tmp_path, archived, chart, is_foundation=True)
    restore_subject._install_recovery(args, tmp_path, archived, chart, is_foundation=False)

    assert len(commands) == 2
    assert all(str(chart) in command for command in commands)
    assert all(str(archived) in command for command in commands)
    assert all("--atomic" in command and "--wait-for-jobs" in command for command in commands)
    assert all("--force-conflicts" in command for command in commands)
    assert str(tmp_path / "recovery-foundation.json") in commands[0]
    assert str(tmp_path / "recovery-foundation.json") not in commands[1]
    foundation = json.loads((tmp_path / "recovery-foundation.json").read_text(encoding="utf-8"))
    assert foundation["migrations"] == {"enabled": False}
    assert foundation["runtimePersistence"] == {"enabled": False}
    assert foundation["secrets"] == {"bootstrapOauthSigningSecret": False}


def test_restore_foundation_reclaims_hibernated_replica_field_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived = tmp_path / "helm-release-values.json"
    archived.write_text(json.dumps(_exact_release_values()), encoding="utf-8")
    chart = tmp_path / "helm-chart.tgz"
    chart.write_bytes(b"exact-chart-package")
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            1,
            b"",
            b'conflict occurred while applying object: conflict with "helm" using apps/v1: .spec.replicas',
        )

    monkeypatch.setattr(restore_subject.subprocess, "run", run)
    args = argparse.Namespace(helm="/qa/bin/helm", recovery_namespace="foundry-qa-recovery")

    with pytest.raises(RuntimeError, match="macmini_restore_foundation_helm_field_conflict"):
        restore_subject._install_recovery(args, tmp_path, archived, chart, is_foundation=True)

    assert len(commands) == 1
    assert "--force-conflicts" in commands[0]
