"""Staging payload construction for Pipeline Graph v2 Media Set outputs."""

from __future__ import annotations

from foundry_lite.application.ports.media_repository import MediaSetRecord
from foundry_lite.application.services.media.uploads import MediaUploadInput
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    MediaOutputEntry,
    MediaOutputTransactionAttempt,
)
from foundry_lite.application.services.pipeline_media_set_output_support import (
    lineage_payload,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
)


def media_upload_input(
    *,
    run_id: str,
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    target: MediaSetRecord,
    transaction_attempt: MediaOutputTransactionAttempt,
    request_fingerprint: str,
    entry: MediaOutputEntry,
) -> MediaUploadInput:
    lineage = lineage_payload(
        run_id,
        node,
        source,
        entry,
        transaction_attempt.generation,
    )
    return MediaUploadInput(
        media_set_id=target.media_set_id,
        media_transaction_id=transaction_attempt.media_transaction_id,
        logical_path=entry.logical_path,
        supplied_mime_type=entry.mime_type,
        schema_type=entry.schema_type,
        format=entry.format,
        security_envelope=dict(entry.security_envelope),
        probe_metadata={"pipelineOutput": lineage},
        source_ref={
            "pipelineOutput": {
                **lineage,
                "requestFingerprint": request_fingerprint,
            }
        },
    )
