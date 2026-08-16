from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.operations import restore_macmini_qa as restore_subject
from scripts.operations.backup_macmini_qa import _worker_identity
from scripts.operations.restore_macmini_qa import _safe_extract


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
