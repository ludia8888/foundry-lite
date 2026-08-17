from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.operations import install_macmini_qa_tool as subject
from scripts.operations.install_macmini_qa_tool import _archive_source


def test_tool_installer_rejects_archive_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="archive_member_invalid"):
        _archive_source(tmp_path / "unused", tmp_path, "../kubectl")


def test_tool_installer_reuses_verified_extracted_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    (qa_root / "bin").mkdir(parents=True)
    (qa_root / "state").mkdir()
    archive = _archive(tmp_path, b"verified-binary")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(subject, "ensure_qa_directories", lambda: None)
    monkeypatch.setattr(subject, "_download", lambda _url, target: target.write_bytes(archive.read_bytes()))

    first = subject.install("helm", "https://example.invalid/helm.tgz", digest, "release/helm")
    second = subject.install("helm", "https://example.invalid/helm.tgz", digest, "release/helm")

    assert first["downloadSha256"] == digest
    assert first["installedFileSha256"] == hashlib.sha256(b"verified-binary").hexdigest()
    assert second["status"] == "already_installed"


def test_tool_installer_rejects_changed_installed_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    (qa_root / "bin").mkdir(parents=True)
    (qa_root / "state").mkdir()
    archive = _archive(tmp_path, b"verified-binary")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(subject, "ensure_qa_directories", lambda: None)
    monkeypatch.setattr(subject, "_download", lambda _url, target: target.write_bytes(archive.read_bytes()))
    subject.install("helm", "https://example.invalid/helm.tgz", digest, "release/helm")
    (qa_root / "bin" / "helm").write_bytes(b"changed")

    with pytest.raises(RuntimeError, match="metadata_mismatch"):
        subject.install("helm", "https://example.invalid/helm.tgz", digest, "release/helm")


def test_tool_installer_rejects_relaxed_existing_binary_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_root = tmp_path / "foundry-qa"
    (qa_root / "bin").mkdir(parents=True)
    (qa_root / "state").mkdir()
    archive = _archive(tmp_path, b"verified-binary")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(subject, "ensure_qa_directories", lambda: None)
    monkeypatch.setattr(subject, "_download", lambda _url, target: target.write_bytes(archive.read_bytes()))
    subject.install("helm", "https://example.invalid/helm.tgz", digest, "release/helm")
    (qa_root / "bin" / "helm").chmod(0o755)

    with pytest.raises(RuntimeError, match="target_conflict"):
        subject.install("helm", "https://example.invalid/helm.tgz", digest, "release/helm")


@pytest.mark.parametrize("name", ["age-keygen", "uv"])
def test_tool_installer_allows_required_bootstrap_tools(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_root = tmp_path / "foundry-qa"
    (qa_root / "bin").mkdir(parents=True)
    (qa_root / "state").mkdir()
    archive = _archive(tmp_path, b"verified-bootstrap-tool")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(subject, "ensure_qa_directories", lambda: None)
    monkeypatch.setattr(subject, "_download", lambda _url, target: target.write_bytes(archive.read_bytes()))

    receipt = subject.install(name, "https://example.invalid/tool.tgz", digest, "release/helm")

    assert receipt["tool"] == name
    assert (qa_root / "bin" / name).read_bytes() == b"verified-bootstrap-tool"


def test_tool_manifest_installs_the_exact_darwin_arm64_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_root = tmp_path / "foundry-qa"
    manifest = qa_root / "repo" / "deploy" / "macmini-tools-arm64.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        (Path("deploy/macmini-tools-arm64.json")).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    installed: list[str] = []

    def install(name: str, _url: str, _digest: str, _member: str | None) -> dict[str, object]:
        installed.append(name)
        return {"tool": name, "status": "installed"}

    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    monkeypatch.setattr(subject, "install", install)

    receipt = subject.install_manifest(str(manifest))

    assert set(installed) == subject._ALLOWED_TOOLS
    assert receipt["platform"] == "darwin-arm64"
    assert receipt["outsideQaRootWritten"] is False


def test_tool_manifest_rejects_a_non_darwin_arm64_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_root = tmp_path / "foundry-qa"
    manifest = qa_root / "repo" / "deploy" / "macmini-tools-arm64.json"
    manifest.parent.mkdir(parents=True)
    text = Path("deploy/macmini-tools-arm64.json").read_text(encoding="utf-8")
    manifest.write_text(text.replace('"darwin-arm64"', '"linux-arm64"'), encoding="utf-8")
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)

    with pytest.raises(ValueError, match="manifest_invalid"):
        subject.install_manifest(str(manifest))


def test_shell_uv_bootstrap_matches_the_pinned_manifest() -> None:
    manifest = json.loads(Path("deploy/macmini-tools-arm64.json").read_text(encoding="utf-8"))
    uv = next(value for value in manifest["tools"] if value["name"] == "uv")
    script = Path("scripts/operations/bootstrap_macmini_qa_uv.sh").read_text(encoding="utf-8")

    assert f'UV_VERSION="{uv["version"]}"' in script
    assert f'UV_ARCHIVE_SHA256="{uv["sha256"]}"' in script
    assert f'UV_ARCHIVE_MEMBER="{uv["archiveMember"]}"' in script
    assert 'EXPECTED_USER="sean1234"' in script
    assert 'EXPECTED_HOME="/Users/sean1234"' in script


def _archive(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "tool.tgz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("release/helm")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return path
