"""Checksum-install one allowlisted QA binary inside sean1234's private bin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from scripts.operations.macmini_qa_guard import QA_ROOT, ensure_qa_directories

_ALLOWED_TOOLS = frozenset({"age", "cosign", "crane", "helm", "kubeconform", "kubectl"})
_MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024


def install(name: str, url: str, expected_sha256: str, archive_member: str | None) -> dict[str, object]:
    ensure_qa_directories()
    if name not in _ALLOWED_TOOLS:
        raise ValueError("macmini_qa_tool_not_allowed")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("macmini_qa_tool_url_invalid")
    if len(expected_sha256) != 64 or any(value not in "0123456789abcdef" for value in expected_sha256):
        raise ValueError("macmini_qa_tool_sha256_invalid")
    target = QA_ROOT / "bin" / name
    if target.exists():
        return _existing_receipt(name, url, expected_sha256, archive_member, target)
    temporary = Path(tempfile.mkdtemp(prefix="tool-", dir=QA_ROOT / "state"))
    try:
        downloaded = temporary / "download"
        _download(url, downloaded)
        if _hash_file(downloaded) != expected_sha256:
            raise RuntimeError("macmini_qa_tool_checksum_mismatch")
        source = _archive_source(downloaded, temporary, archive_member)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as stream:
            shutil.copyfileobj(stream, output, length=1024 * 1024)
        receipt = _receipt(name, url, archive_member, target, expected_sha256, "installed")
        _write_metadata(name, receipt)
        return receipt
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"user-agent": "Foundry-lite-enterprise-QA/1"})
    with (
        urllib.request.urlopen(  # nosec B310 - HTTPS URL is validated; remove if non-HTTPS sources are allowed.
            request, timeout=60
        ) as response,
        target.open("xb") as output,
    ):
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_DOWNLOAD_BYTES:
                raise RuntimeError("macmini_qa_tool_download_too_large")
            output.write(chunk)
    os.chmod(target, 0o600)


def _archive_source(downloaded: Path, temporary: Path, member_name: str | None) -> Path:
    if member_name is None:
        return downloaded
    if member_name.startswith("/") or ".." in Path(member_name).parts:
        raise ValueError("macmini_qa_tool_archive_member_invalid")
    with tarfile.open(downloaded, mode="r:gz") as archive:
        member = archive.getmember(member_name)
        if not member.isfile() or member.size <= 0 or member.size > _MAX_DOWNLOAD_BYTES:
            raise RuntimeError("macmini_qa_tool_archive_member_invalid")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("macmini_qa_tool_archive_member_missing")
        extracted = temporary / "extracted"
        with source, extracted.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    os.chmod(extracted, 0o600)
    return extracted


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_receipt(
    name: str,
    url: str,
    expected_sha256: str,
    archive_member: str | None,
    target: Path,
) -> dict[str, object]:
    metadata = _metadata_path(name)
    if not target.is_file() or not metadata.is_file() or metadata.stat().st_mode & 0o077:
        raise RuntimeError("macmini_qa_tool_target_conflict")
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("macmini_qa_tool_metadata_invalid") from exc
    expected = _receipt(name, url, archive_member, target, expected_sha256, "already_installed")
    immutable_keys = ("tool", "sourceUrl", "archiveMember", "downloadSha256", "installedFileSha256")
    if not isinstance(value, dict) or any(value.get(key) != expected.get(key) for key in immutable_keys):
        raise RuntimeError("macmini_qa_tool_metadata_mismatch")
    return expected


def _receipt(
    name: str,
    url: str,
    archive_member: str | None,
    target: Path,
    expected_sha256: str,
    status: str,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": status,
        "tool": name,
        "path": str(target),
        "sourceUrl": url,
        "archiveMember": archive_member,
        "downloadSha256": expected_sha256,
        "installedFileSha256": _hash_file(target),
        "outsideQaRootWritten": False,
    }


def _metadata_path(name: str) -> Path:
    return QA_ROOT / "state" / f"tool-install-{name}.json"


def _write_metadata(name: str, receipt: dict[str, object]) -> None:
    path = _metadata_path(name)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--archive-member")
    args = parser.parse_args()
    receipt = install(args.name, args.url, args.sha256, args.archive_member)
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
