"""Serving artifact payload for a committed Pipeline Graph v2 Media Set output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaSetRecord,
)
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    JsonObject,
    MediaOutputEntry,
    MediaOutputTransactionAttempt,
)
from foundry_lite.application.services.pipeline_media_set_output_support import (
    pipeline_output_lineage,
    versions_by_entry,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
    artifact_input_refs,
)
from foundry_lite.application.services.pipeline_v2_runtime_media_payloads import (
    latest_committed_at,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import (
    inherited_runtime_security,
)


def output_artifact(
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    target_ref: str,
    target: MediaSetRecord,
    transaction_attempt: MediaOutputTransactionAttempt,
    entries: Sequence[MediaOutputEntry],
    versions: Sequence[MediaItemVersionRecord],
) -> PipelineV2RuntimeArtifact:
    items = _output_items(target_ref, target, entries, versions)
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="media",
        artifact_kind="media_set_selection",
        plane="media",
        items=items,
        artifact_ref=_artifact_ref(target_ref, target, transaction_attempt, items),
        manifest=_manifest(source, inputs, transaction_attempt, entries, items),
        security_envelope=inherited_runtime_security([source]),
        status="COMMITTED",
        is_serving=True,
        committed_at=latest_committed_at(items),
    )


def _output_items(
    target_ref: str,
    target: MediaSetRecord,
    entries: Sequence[MediaOutputEntry],
    versions: Sequence[MediaItemVersionRecord],
) -> tuple[JsonObject, ...]:
    by_entry = versions_by_entry(versions)
    return tuple(_output_item(target_ref, target, entry, by_entry[entry.entry_fingerprint]) for entry in entries)


def _output_item(
    target_ref: str,
    target: MediaSetRecord,
    entry: MediaOutputEntry,
    version: MediaItemVersionRecord,
) -> JsonObject:
    return {
        "mediaSetRef": target_ref,
        "mediaSetId": target.media_set_id,
        "mediaItemVersionId": version.media_item_version_id,
        "mediaItemId": version.media_item_id,
        "logicalPath": entry.logical_path,
        "contentHash": version.content_hash,
        "byteSize": version.byte_size,
        "format": version.format,
        "mimeType": version.sniffed_mime_type,
        "committedAt": version.committed_at,
        "securityEnvelope": dict(version.security_envelope),
        "sourceLineage": dict(pipeline_output_lineage(version)),
    }


def _artifact_ref(
    target_ref: str,
    target: MediaSetRecord,
    transaction_attempt: MediaOutputTransactionAttempt,
    items: Sequence[Mapping[str, object]],
) -> JsonObject:
    return {
        "mediaSetRef": target_ref,
        "mediaSetId": target.media_set_id,
        "mediaTransactionId": transaction_attempt.media_transaction_id,
        "mediaTransactionGeneration": transaction_attempt.generation,
        "mediaItemVersionIds": [item["mediaItemVersionId"] for item in items],
    }


def _manifest(
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    transaction_attempt: MediaOutputTransactionAttempt,
    entries: Sequence[MediaOutputEntry],
    items: Sequence[Mapping[str, object]],
) -> JsonObject:
    return {
        "inputArtifacts": artifact_input_refs(inputs),
        "sourceArtifactKind": source.artifact_kind,
        "itemCount": len(items),
        "commitKind": "SERVING_ASSET",
        "commitProtocol": ["stage", "validate", "commit"],
        "transactionGeneration": transaction_attempt.generation,
        "lineage": [_lineage_manifest(entry, item) for entry, item in zip(entries, items, strict=True)],
    }


def _lineage_manifest(entry: MediaOutputEntry, item: Mapping[str, object]) -> JsonObject:
    return {
        "sourceMediaItemVersionId": entry.source_media_item_version_id,
        "mediaDerivativeId": entry.media_derivative_id,
        "targetMediaItemVersionId": item["mediaItemVersionId"],
        "entryFingerprint": entry.entry_fingerprint,
    }
