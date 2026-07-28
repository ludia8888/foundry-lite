"""Repository-backed resolver for immutable model media inputs."""

from __future__ import annotations

import hashlib

from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureKind
from foundry_lite.application.ports.language_model import (
    ModelMediaContent,
    ModelMediaReference,
)
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord, MediaRepository
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.ports.transaction_context import TransactionManager

_DEFAULT_MAX_MEDIA_BYTES = 24 * 1024 * 1024


class RepositoryModelMediaResolver:
    """Read committed media only after tenant, classification, MIME, and hash verification."""

    profile_name = "repository-model-media"

    def __init__(
        self,
        engine: TransactionManager,
        media_repository: MediaRepository,
        media_storage: MediaStorageAdapter,
        *,
        max_media_bytes: int = _DEFAULT_MAX_MEDIA_BYTES,
    ) -> None:
        self._engine = engine
        self._media_repository = media_repository
        self._media_storage = media_storage
        self._max_media_bytes = max_media_bytes

    def read(
        self,
        *,
        tenant_id: str,
        reference: ModelMediaReference,
        expected_classification: str,
    ) -> ModelMediaContent:
        version = self._version(tenant_id, reference.media_item_version_id)
        self._guard_version(version, reference, expected_classification)
        self._guard_storage(version)
        content = self._read_bytes(version)
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != _normalized_hash(version.content_hash):
            raise _failure("conflict", "model media bytes failed immutable hash verification", "media_hash_mismatch")
        return ModelMediaContent(
            media_item_version_id=version.media_item_version_id,
            mime_type=_resolved_mime_type(version),
            content_hash=f"sha256:{actual_hash}",
            byte_size=len(content),
            content=content,
        )

    def _version(self, tenant_id: str, media_item_version_id: str) -> MediaItemVersionRecord:
        with self._engine.begin() as transaction:
            version = self._media_repository.media_item_version_by_id(
                transaction=transaction,
                tenant_id=tenant_id,
                media_item_version_id=media_item_version_id,
            )
        if version is None:
            raise _failure("not_found", "model media version was not found", "media_version_not_found")
        return version

    def _guard_version(
        self,
        version: MediaItemVersionRecord,
        reference: ModelMediaReference,
        expected_classification: str,
    ) -> None:
        if version.status != "COMMITTED":
            raise _failure("not_found", "model media version is not committed", "media_version_not_committed")
        if _normalized_hash(reference.content_hash) != _normalized_hash(version.content_hash):
            raise _failure("conflict", "model media reference hash does not match catalog truth", "media_hash_mismatch")
        classification = version.security_envelope.get("classification")
        if classification != expected_classification:
            raise _failure(
                "authorization",
                "model media classification does not match the governed egress request",
                "media_classification_mismatch",
            )
        if _normalized_mime(reference.mime_type) != _normalized_mime(_resolved_mime_type(version)):
            raise _failure("conflict", "model media MIME does not match catalog truth", "media_mime_mismatch")
        if version.byte_size > self._max_media_bytes:
            raise _failure("validation", "model media exceeds the bounded provider input limit", "media_too_large")

    def _guard_storage(self, version: MediaItemVersionRecord) -> None:
        stat = self._media_storage.stat(version.blob_key)
        if not stat.is_present:
            raise _failure("not_found", "committed model media bytes are missing", "media_storage_missing")
        if stat.byte_size != version.byte_size:
            raise _failure("conflict", "committed model media size does not match catalog truth", "media_size_mismatch")
        if _normalized_hash(stat.content_hash) != _normalized_hash(version.content_hash):
            raise _failure("conflict", "committed model media hash does not match catalog truth", "media_hash_mismatch")

    def _read_bytes(self, version: MediaItemVersionRecord) -> bytes:
        stream = self._media_storage.open_stream(version.blob_key)
        try:
            content = stream.read(self._max_media_bytes + 1)
        finally:
            stream.close()
        if len(content) > self._max_media_bytes:
            raise _failure("validation", "model media exceeds the bounded provider input limit", "media_too_large")
        return content


def _resolved_mime_type(version: MediaItemVersionRecord) -> str:
    sniffed = _normalized_mime(version.sniffed_mime_type)
    return version.supplied_mime_type if sniffed == "application/octet-stream" else sniffed


def _normalized_mime(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _normalized_hash(value: str) -> str:
    return value.removeprefix("sha256:")


def _failure(kind: AdapterFailureKind, message: str, reason: str) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="repository-model-media",
            operation="read",
            kind=kind,
            is_retryable=False,
            operator_message=message,
            details={"reason": reason},
        )
    )
