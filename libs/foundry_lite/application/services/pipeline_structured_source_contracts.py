"""Committed stream and geospatial source contracts for Pipeline Graph v2."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.source_management_repository import (
    SourceSyncRow,
    SourceSyncRunRow,
)
from foundry_lite.application.services.pipeline_geospatial_contracts import (
    geospatial_spec,
)
from foundry_lite.application.services.pipeline_graph_contracts import (
    PipelineArtifactKind,
    PipelineV2Node,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceContract,
    PipelineSourceVersionPin,
)


def build_stream_source_contract(
    *,
    node: PipelineV2Node,
    source_ref: str,
    sync: SourceSyncRow,
    run: SourceSyncRunRow,
    dataset_contract: PipelineSourceContract,
) -> PipelineSourceContract:
    """Pin one committed micro-batch and its durable broker checkpoint."""

    dataset_pin = dataset_contract.version_pins[0]
    metadata = _stream_metadata(sync, run, dataset_contract)
    pin = PipelineSourceVersionPin(
        version_id=dataset_pin.version_id,
        ordinal=dataset_pin.ordinal,
        content_fingerprint=dataset_pin.content_fingerprint,
        metadata=metadata,
    )
    return PipelineSourceContract(
        node_id=node["id"],
        descriptor_id=node["descriptorId"],
        artifact_kind=PipelineArtifactKind.STREAM_CHECKPOINT,
        resource_ref=source_ref,
        source_id=sync["id"],
        schema_contract=dataset_contract.schema_contract,
        schema_hash=dataset_contract.schema_hash,
        schema_version=dataset_contract.schema_version,
        version_pins=(pin,),
        security_envelope=dataset_contract.security_envelope,
        access_evidence=dataset_contract.access_evidence,
    )


def is_committed_stream_run(run: Mapping[str, object]) -> bool:
    """Return whether a source run has both immutable data and offset truth."""

    version_id = run.get("dataset_version_id")
    checkpoint = run.get("checkpoint_end")
    return (
        str(run.get("status") or "").lower() == "succeeded"
        and isinstance(version_id, str)
        and bool(version_id.strip())
        and isinstance(checkpoint, Mapping)
        and bool(checkpoint)
    )


def _stream_metadata(
    sync: SourceSyncRow,
    run: SourceSyncRunRow,
    dataset_contract: PipelineSourceContract,
) -> dict[str, object]:
    return {
        **dict(dataset_contract.version_pins[0].metadata),
        "backingDatasetRef": dataset_contract.resource_ref,
        "backingDatasetId": dataset_contract.source_id,
        "sourceSyncId": sync["id"],
        "sourceSyncRunId": run["id"],
        "sourceName": sync["source_name"],
        "sourceType": sync["source_type"],
        "checkpointStart": dict(run["checkpoint_start"]),
        "checkpointEnd": dict(run["checkpoint_end"]),
    }


def build_geospatial_source_contract(
    *,
    node: PipelineV2Node,
    resource_ref: str,
    dataset_contract: PipelineSourceContract,
) -> PipelineSourceContract:
    """Promote one exact Dataset version as a validated geospatial series."""

    dataset_pin = dataset_contract.version_pins[0]
    metadata = {
        **dict(dataset_pin.metadata),
        "backingDatasetRef": dataset_contract.resource_ref,
        "backingDatasetId": dataset_contract.source_id,
        "geospatialSpec": geospatial_spec(dataset_contract.schema_contract, node["config"]),
    }
    pin = PipelineSourceVersionPin(
        version_id=dataset_pin.version_id,
        ordinal=dataset_pin.ordinal,
        content_fingerprint=dataset_pin.content_fingerprint,
        metadata=metadata,
    )
    return PipelineSourceContract(
        node_id=node["id"],
        descriptor_id=node["descriptorId"],
        artifact_kind=PipelineArtifactKind.GEOSPATIAL_SERIES,
        resource_ref=resource_ref,
        source_id=dataset_contract.source_id,
        schema_contract=dataset_contract.schema_contract,
        schema_hash=dataset_contract.schema_hash,
        schema_version=dataset_contract.schema_version,
        version_pins=(pin,),
        security_envelope=dataset_contract.security_envelope,
        access_evidence=dataset_contract.access_evidence,
    )
