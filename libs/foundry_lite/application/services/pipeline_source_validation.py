"""Graph validation that replaces draft source schemas with committed truth."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_graph_contracts import JsonObject
from foundry_lite.application.services.pipeline_graph_model import (
    pipeline_graph_fingerprint,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_graph_v2_validation import validate_pipeline_graph_v2
from foundry_lite.application.services.pipeline_source_contract_resolver import (
    PipelineSourceContractResolutionFailed,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceResolution,
    apply_source_contract_schemas,
    pipeline_source_contract_payload,
)

_DRAFT_SCHEMA_ERROR_CODES = frozenset({"join_key_missing", "union_schema_mismatch"})


def validate_pipeline_graph_with_sources(
    graph: Mapping[str, object],
    resolution: PipelineSourceResolution,
) -> JsonObject:
    """Validate topology/config using exact committed schemas for source nodes."""

    base = validate_pipeline_graph(graph)
    canonical = normalize_pipeline_graph(graph)
    effective = apply_source_contract_schemas(canonical, resolution.contracts)
    committed = validate_pipeline_graph_v2(effective)
    errors = _merged_rows(
        _without_draft_schema_errors(_rows(base.get("errors"))),
        _rows(committed.get("errors")),
    )
    warnings = _merged_rows(
        _rows(base.get("warnings")),
        _rows(committed.get("warnings")),
        list(resolution.warnings),
    )
    result = dict(base)
    result.update(
        {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "fingerprint": pipeline_graph_fingerprint(graph),
            "normalizedGraph": canonical,
            "sourceContracts": [pipeline_source_contract_payload(contract) for contract in resolution.contracts],
        }
    )
    return result


def validation_with_source_failure(
    graph: Mapping[str, object],
    failure: PipelineSourceContractResolutionFailed,
) -> JsonObject:
    """Convert a typed repository failure into the existing validation payload."""

    base = validate_pipeline_graph(graph)
    error = {
        "code": str(failure.details.get("reason") or "source_resolution_failed"),
        **{str(key): value for key, value in failure.details.items()},
    }
    errors = _merged_rows(_rows(base.get("errors")), [error])
    result = dict(base)
    result["valid"] = False
    result["errors"] = errors
    result["sourceContracts"] = []
    return result


def _without_draft_schema_errors(rows: Sequence[JsonObject]) -> list[JsonObject]:
    return [row for row in rows if row.get("code") not in _DRAFT_SCHEMA_ERROR_CODES]


def _rows(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [{str(key): item for key, item in row.items()} for row in value if isinstance(row, Mapping)]


def _merged_rows(*groups: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    result: list[JsonObject] = []
    seen: set[str] = set()
    for group in groups:
        for row in group:
            payload = {str(key): value for key, value in row.items()}
            fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
            if fingerprint not in seen:
                result.append(payload)
                seen.add(fingerprint)
    return result
