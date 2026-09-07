"""Large generated files must not make Compass discovery unusable over MCP."""

from __future__ import annotations

from copy import deepcopy

import pytest
from foundry_lite.application.services.aip.fde_compass_tool_projections import compass_tool_result
from foundry_lite.application.services.aip.fde_tool_result import hash_json


@pytest.mark.parametrize("key", ["items", "resources", "folders", "projects"])
def test_compass_collection_summarizes_large_metadata_without_mutating_records(key: str) -> None:
    metadata = {"definition": {"pages": ["업무 화면" * 500]}, "reactFiles": {"App.tsx": "x" * 70000}}
    record = {"rid": "ri.foundry-lite.workshop-app.example", "displayName": "병원", "metadata": metadata}
    payload = {key: [record], "nextCursor": "preserved-cursor", "count": 1}
    before = deepcopy(payload)

    output = compass_tool_result(payload)

    assert payload == before
    assert output["nextCursor"] == "preserved-cursor"
    assert output["count"] == 1
    summary = output[key][0]
    assert summary["rid"] == record["rid"]
    assert "metadata" not in summary
    assert summary["metadataSummary"]["fingerprint"] == hash_json(metadata)
    assert summary["metadataSummary"]["isContentIncluded"] is False
    assert summary["metadataSummary"]["fieldNames"] == ["definition", "reactFiles"]
    assert summary["metadataRetrievePath"] == f"/api/resources/{record['rid']}"


@pytest.mark.parametrize("key", ["resource", "project", "folder"])
def test_compass_single_record_preserves_small_metadata(key: str) -> None:
    payload = {key: {"id": "record-1", "metadata": {"status": "active", "datasetRef": "seed.patients"}}}
    assert compass_tool_result(payload) == payload


def test_compass_metadata_summary_bounds_field_names() -> None:
    metadata = {f"field-{index:03}": "x" * 500 for index in range(100)}
    output = compass_tool_result({"resource": {"rid": "ri.example", "metadata": metadata}})
    summary = output["resource"]["metadataSummary"]
    assert summary["fieldCount"] == 100
    assert len(summary["fieldNames"]) == 20
    assert summary["fingerprint"] == hash_json(metadata)
