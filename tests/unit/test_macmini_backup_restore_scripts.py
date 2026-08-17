from __future__ import annotations

import argparse
import io
import json
import subprocess
import tarfile
from pathlib import Path

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


def test_backup_freezes_commit_point_after_platform_evidence_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    target = tmp_path / "backup"
    target.mkdir()

    def api_json(_base: str, _token: str, path: str, _payload: object) -> dict[str, object]:
        events.append(path)
        return {"status": "paused"} if path.endswith("/start") else {}

    class CommitPointCaptured(RuntimeError):
        pass

    def postgres_inventory(_args: argparse.Namespace) -> object:
        events.append("postgres-inventory")
        raise CommitPointCaptured

    monkeypatch.setattr(backup_subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(backup_subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(backup_subject, "_private_text", lambda _path: "operator-token")
    monkeypatch.setattr(backup_subject, "_age_recipient", lambda _path: "age1recipient")
    monkeypatch.setattr(backup_subject, "_backup_directory", lambda _run_id: target)
    monkeypatch.setattr(backup_subject, "_api_json", api_json)
    monkeypatch.setattr(backup_subject, "_pause_workers", lambda _args: [])
    monkeypatch.setattr(backup_subject, "_helm_release_values", lambda _args: _exact_release_values())
    monkeypatch.setattr(backup_subject, "_package_release_chart", lambda *_args: target / "helm-chart.tgz")
    monkeypatch.setattr(backup_subject, "_postgres_inventory", postgres_inventory)
    args = argparse.Namespace(
        namespace="foundry-qa",
        bearer_token_file=str(tmp_path / "token"),
        age_recipient_file=str(tmp_path / "recipient"),
        run_id="run-1",
        api_base_url="http://127.0.0.1:30443",
    )

    with pytest.raises(CommitPointCaptured):
        backup_subject.backup(args)

    assert events == [
        "/api/operations/backup-restore/restore-mode/start",
        "/api/operations/backup-restore/preflight",
        "/api/operations/backup-restore/artifacts",
        "postgres-inventory",
    ]


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
    assert str(tmp_path / "recovery-foundation.json") in commands[0]
    assert str(tmp_path / "recovery-foundation.json") not in commands[1]
    foundation = json.loads((tmp_path / "recovery-foundation.json").read_text(encoding="utf-8"))
    assert foundation["migrations"] == {"enabled": False}
    assert foundation["runtimePersistence"] == {"enabled": False}
    assert foundation["secrets"] == {"bootstrapOauthSigningSecret": False}
