"""Small Protocol contracts used by source onboarding helper functions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO, Protocol

from foundry_lite.application.ports import DatasetRow, TransactionContext
from foundry_lite.application.ports.media_storage import UploadSession
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.media.transactions import MediaCommitResult
from foundry_lite.application.services.media.uploads import MediaUploadInput, StagedUpload
from foundry_lite.domain.context import RequestContext


class DatasetRegistryBoundary(Protocol):
    """Dataset registry capability required by source onboarding."""

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        partition_spec: list[str] | None = None,
        sort_order: list[str] | None = None,
        target_file_size_bytes: int | None = None,
    ) -> DatasetRow: ...


class DatasetCsvIngestBoundary(Protocol):
    """CSV ingest capability required by file-backed source onboarding."""

    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult: ...


class MediaTransactionBoundary(Protocol):
    """Media transaction capability required by media source onboarding."""

    def open(
        self,
        ctx: RequestContext,
        *,
        media_set_id: str,
        idempotency_key: str,
        mode: str = "APPEND",
        request_fingerprint: str = "",
    ) -> str: ...

    def commit(self, ctx: RequestContext, *, media_transaction_id: str) -> MediaCommitResult: ...


class MediaUploadBoundary(Protocol):
    """Media upload capability required by media source onboarding."""

    def initiate(
        self,
        ctx: RequestContext,
        *,
        media_set_id: str,
        logical_path: str,
        supplied_mime_type: str,
    ) -> UploadSession: ...

    def complete(
        self,
        ctx: RequestContext,
        *,
        inputs: MediaUploadInput,
        upload: UploadSession,
        source: BinaryIO,
    ) -> StagedUpload: ...


class SourcePolicyBoundary(Protocol):
    """Policy capability required before source write operations."""

    def require(self, ctx: RequestContext, permission: str) -> None: ...


class SourceRuntimeBoundary(Protocol):
    """Runtime audit and write-traffic capability used by source services."""

    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...
