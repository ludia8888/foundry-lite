"""Commit receipts and typed reconciliation for Media Set pipeline outputs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from foundry_lite.application.services.media.transactions import MediaCommitResult
from foundry_lite.application.services.pipeline_media_output_port_types import (
    MediaItemVersionRecord,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2CommittedOutputReconciliationRequired,
    PipelineV2RuntimeArtifact,
)
from foundry_lite.domain.errors import ConflictDetected


def committed_media_versions(
    versions: Sequence[MediaItemVersionRecord],
    committed_at: str | None,
) -> tuple[MediaItemVersionRecord, ...]:
    return tuple(replace(version, status="COMMITTED", committed_at=committed_at) for version in versions)


def require_media_commit_receipt(
    result: MediaCommitResult,
    versions: Sequence[MediaItemVersionRecord],
) -> None:
    expected_ids = {version.media_item_version_id for version in versions}
    if set(result.committed_version_ids) == expected_ids:
        return
    raise ConflictDetected(
        "pipeline Media Set commit receipt does not match its prevalidated versions",
        details={
            "mediaTransactionId": result.media_transaction_id,
            "expectedVersionIds": sorted(expected_ids),
            "actualVersionIds": sorted(result.committed_version_ids),
        },
    )


def raise_media_output_reconciliation(
    artifact: PipelineV2RuntimeArtifact,
    exc: Exception,
) -> None:
    raise PipelineV2CommittedOutputReconciliationRequired(
        "pipeline Media Set output committed but secondary validation failed",
        artifact=artifact,
    ) from exc
