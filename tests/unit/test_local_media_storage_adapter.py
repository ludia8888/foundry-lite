"""Behaviour of the local filesystem media storage adapter (ADR-0001 §5.1, doc §6.1).

The local profile lands bytes in-process and treats commit as a pointer flip. These
cases pin staging, measured stat/hash, range reads, MIME sniffing, idempotent commit,
orphan deletion, and read grants so the S3 profile can later swap in behind the port.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from foundry_lite.application.ports.media_storage import (
    ByteRange,
    CompleteMediaUpload,
    InitiateMediaUpload,
    MediaReadGrantRequest,
)
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter


@pytest.fixture
def adapter(tmp_path: Path) -> LocalMediaStorageAdapter:
    return LocalMediaStorageAdapter(tmp_path / "media")


def _stage(adapter: LocalMediaStorageAdapter, body: bytes, *, logical_path: str = "/a.pdf") -> str:
    session = adapter.initiate_upload(
        InitiateMediaUpload(
            tenant_id="t", media_set_id="ms", logical_path=logical_path, supplied_mime_type="application/pdf"
        )
    )
    adapter.write_staged(session.staged_object_key, io.BytesIO(body))
    return session.staged_object_key


def test_complete_measures_size_hash_and_sniffs_pdf(adapter: LocalMediaStorageAdapter) -> None:
    key = _stage(adapter, b"%PDF-1.4 hello")
    staged = adapter.complete_staged_upload(CompleteMediaUpload(upload_id="up", staged_object_key=key))
    assert staged.byte_size == len(b"%PDF-1.4 hello")
    assert staged.sniffed_mime_type == "application/pdf"
    assert len(staged.content_hash) == 64  # sha256 hex


@pytest.mark.parametrize(
    ("body", "expected_mime"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"plain bytes", "application/octet-stream"),
    ],
)
def test_complete_sniffs_known_magic_bytes(adapter: LocalMediaStorageAdapter, body: bytes, expected_mime: str) -> None:
    key = _stage(adapter, body)
    staged = adapter.complete_staged_upload(CompleteMediaUpload(upload_id="up", staged_object_key=key))
    assert staged.sniffed_mime_type == expected_mime


def test_stat_reports_absent_blob_as_not_present(adapter: LocalMediaStorageAdapter) -> None:
    stat = adapter.stat("missing/blob")
    assert stat.is_present is False
    assert stat.byte_size == 0
    assert stat.content_hash == ""


def test_stat_reports_present_blob(adapter: LocalMediaStorageAdapter) -> None:
    key = _stage(adapter, b"%PDF-1.4 hello")
    stat = adapter.stat(key)
    assert stat.is_present is True
    assert stat.byte_size == len(b"%PDF-1.4 hello")


def test_open_stream_full_and_ranged(adapter: LocalMediaStorageAdapter) -> None:
    key = _stage(adapter, b"0123456789")
    with adapter.open_stream(key) as full:
        assert full.read() == b"0123456789"
    with adapter.open_stream(key, ByteRange(start=4, end=6)) as ranged:
        assert ranged.read() == b"456789"


def test_commit_reference_moves_blob_and_is_idempotent(adapter: LocalMediaStorageAdapter) -> None:
    key = _stage(adapter, b"%PDF-1.4 hello")
    committed = adapter.commit_reference(key, "committed/a.pdf")
    assert committed.object_key == "committed/a.pdf"
    assert committed.byte_size == len(b"%PDF-1.4 hello")
    assert adapter.stat("committed/a.pdf").is_present is True
    assert adapter.stat(key).is_present is False
    # A replayed commit to an already-committed key is a no-op, not a failure.
    again = adapter.commit_reference(key, "committed/a.pdf")
    assert again.content_hash == committed.content_hash


def test_issue_read_grant_inlines_a_stream(adapter: LocalMediaStorageAdapter) -> None:
    key = _stage(adapter, b"%PDF-1.4 hello")
    grant = adapter.issue_read_grant(MediaReadGrantRequest(tenant_id="t", media_item_version_id="v", object_key=key))
    assert grant.grant_kind == "inline"
    assert grant.stream is not None
    with grant.stream as stream:
        assert stream.read() == b"%PDF-1.4 hello"


def test_delete_uncommitted_removes_blob(adapter: LocalMediaStorageAdapter) -> None:
    key = _stage(adapter, b"%PDF-1.4 hello")
    adapter.delete_uncommitted(key)
    assert adapter.stat(key).is_present is False
    # Deleting an already-absent blob is a no-op.
    adapter.delete_uncommitted(key)


def test_profile_name_is_local(adapter: LocalMediaStorageAdapter) -> None:
    assert adapter.profile_name == "local"
