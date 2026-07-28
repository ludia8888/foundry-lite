from __future__ import annotations

import hashlib
import io
from contextlib import nullcontext

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.language_model import ModelMediaReference
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.ports.media_storage import MediaObjectStat
from foundry_lite.infrastructure.adapters.model_media_resolver import RepositoryModelMediaResolver


class _Engine:
    def begin(self):  # type: ignore[no-untyped-def]
        return nullcontext(object())


class _Repository:
    def __init__(self, version: MediaItemVersionRecord | None) -> None:
        self.version = version

    def media_item_version_by_id(self, **_kwargs):  # type: ignore[no-untyped-def]
        return self.version


class _Storage:
    profile_name = "test-media"

    def __init__(self, content: bytes, *, stat_hash: str | None = None) -> None:
        self.content = content
        self.stat_hash = stat_hash or hashlib.sha256(content).hexdigest()

    def stat(self, object_key: str) -> MediaObjectStat:
        return MediaObjectStat(
            object_key=object_key,
            byte_size=len(self.content),
            content_hash=self.stat_hash,
            is_present=True,
        )

    def open_stream(self, _object_key: str):  # type: ignore[no-untyped-def]
        return io.BytesIO(self.content)


def _version(content: bytes, *, classification: str = "public") -> MediaItemVersionRecord:
    digest = hashlib.sha256(content).hexdigest()
    return MediaItemVersionRecord(
        media_item_version_id="media-v1",
        tenant_id="tenant-1",
        media_item_id="item-1",
        media_transaction_id="tx-1",
        version_number=1,
        blob_key="committed/media-v1.pdf",
        content_hash=digest,
        byte_size=len(content),
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={},
        security_envelope={"classification": classification},
        source_ref=None,
        status="COMMITTED",
        created_at="2026-07-16T00:00:00Z",
        committed_at="2026-07-16T00:00:01Z",
    )


def _reference(content: bytes) -> ModelMediaReference:
    return ModelMediaReference(
        media_item_version_id="media-v1",
        mime_type="application/pdf",
        content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


def test_repository_model_media_resolver_returns_verified_committed_bytes() -> None:
    content = b"%PDF-1.4 governed media"
    resolver = RepositoryModelMediaResolver(_Engine(), _Repository(_version(content)), _Storage(content))

    resolved = resolver.read(
        tenant_id="tenant-1",
        reference=_reference(content),
        expected_classification="public",
    )

    assert resolved.content == content
    assert resolved.mime_type == "application/pdf"
    assert resolved.content_hash == f"sha256:{hashlib.sha256(content).hexdigest()}"


def test_repository_model_media_resolver_fails_closed_on_classification_mismatch() -> None:
    content = b"%PDF-1.4 governed media"
    resolver = RepositoryModelMediaResolver(
        _Engine(),
        _Repository(_version(content, classification="internal")),
        _Storage(content),
    )

    with pytest.raises(AdapterError) as excinfo:
        resolver.read(
            tenant_id="tenant-1",
            reference=_reference(content),
            expected_classification="public",
        )

    assert excinfo.value.failure.kind == "authorization"
    assert excinfo.value.failure.details["reason"] == "media_classification_mismatch"


def test_repository_model_media_resolver_detects_storage_hash_drift() -> None:
    content = b"%PDF-1.4 governed media"
    resolver = RepositoryModelMediaResolver(
        _Engine(),
        _Repository(_version(content)),
        _Storage(content, stat_hash="different"),
    )

    with pytest.raises(AdapterError) as excinfo:
        resolver.read(
            tenant_id="tenant-1",
            reference=_reference(content),
            expected_classification="public",
        )

    assert excinfo.value.failure.kind == "conflict"
    assert excinfo.value.failure.details["reason"] == "media_hash_mismatch"


def test_repository_model_media_resolver_enforces_bound_before_read() -> None:
    content = b"%PDF-1.4 governed media"
    resolver = RepositoryModelMediaResolver(
        _Engine(),
        _Repository(_version(content)),
        _Storage(content),
        max_media_bytes=4,
    )

    with pytest.raises(AdapterError) as excinfo:
        resolver.read(
            tenant_id="tenant-1",
            reference=_reference(content),
            expected_classification="public",
        )

    assert excinfo.value.failure.details["reason"] == "media_too_large"
