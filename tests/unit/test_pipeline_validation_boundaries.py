from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports.media_repository import MediaSetRecord
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.services.pipeline_dataset_version_read_contracts import (
    ExactDatasetVersionReadFailed,
    ExactDatasetVersionReadRequest,
)
from foundry_lite.application.services.pipeline_dataset_version_read_validation import (
    validate_exact_dataset_request,
)
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    MediaOutputEntry,
    PipelineMediaSetOutputContractMismatch,
)
from foundry_lite.application.services.pipeline_media_set_output_support import (
    validate_output_versions,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceResolution,
)
from foundry_lite.application.services.pipeline_source_validation import (
    validate_pipeline_graph_with_sources,
)


def test_exact_dataset_request_rejects_a_non_dataset_descriptor() -> None:
    request = ExactDatasetVersionReadRequest(
        node_id="source",
        descriptor_id="source.media_set",
        artifact_kind="dataset_version",
        dataset_ref="raw.orders",
        dataset_id="dataset-1",
        version_id="version-1",
        version_number=1,
        content_fingerprint="fingerprint",
        version_metadata={},
        schema_contract={},
        schema_hash="schema-hash",
        schema_version=1,
        security_envelope={},
        access_evidence={},
    )

    with pytest.raises(ExactDatasetVersionReadFailed) as exc:
        validate_exact_dataset_request(request)

    assert exc.value.details["reason"] == "source_descriptor_not_dataset"


def test_media_output_validation_rejects_a_missing_staged_entry() -> None:
    entry = MediaOutputEntry(
        entry_fingerprint="entry-1",
        source_media_item_version_id="source-version-1",
        media_derivative_id=None,
        logical_path="document.pdf",
        blob_key="blob",
        content_hash="a" * 64,
        byte_size=1,
        mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        primary_format="pdf",
        allowed_formats=("pdf",),
        security_envelope={"classification": "INTERNAL"},
    )
    target = cast(MediaSetRecord, cast(object, _MediaSetIdentity("media-set-1")))
    storage = cast(MediaStorageAdapter, cast(object, _UnusedStorage()))

    with pytest.raises(PipelineMediaSetOutputContractMismatch):
        validate_output_versions([], [entry], target, "request", 1, storage)


def test_source_validation_never_invents_committed_source_contracts() -> None:
    graph = {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "source",
                "kind": "source",
                "descriptorId": "source.dataset",
                "specVersion": 1,
                "config": {"datasetRef": "raw.orders", "schema": []},
            },
            {
                "id": "output",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "clean.orders"},
            },
        ],
        "edges": [
            {
                "id": "source-output",
                "sourceNodeId": "source",
                "sourcePortId": "dataset",
                "targetNodeId": "output",
                "targetPortId": "input",
            }
        ],
        "layout": {},
        "outputContract": {},
        "tests": [],
        "schedule": None,
    }

    result = validate_pipeline_graph_with_sources(graph, PipelineSourceResolution(contracts=()))

    assert result["valid"] is True
    assert result["sourceContracts"] == []


class _MediaSetIdentity:
    def __init__(self, media_set_id: str) -> None:
        self.media_set_id = media_set_id


class _UnusedStorage:
    pass
