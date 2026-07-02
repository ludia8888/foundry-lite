"""Pure restore-mode approval rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.errors import ConflictDetected


def post_restore_validation_findings(preflight: Mapping[str, object]) -> list[dict[str, object]]:
    """Return findings that must be resolved before publisher resume."""
    findings: list[dict[str, object]] = []
    if preflight.get("status") != "ready":
        findings.append({"check": "metadata_storage_preflight", "status": preflight.get("status")})
    if not _has_items(preflight.get("datasetVersions")):
        findings.append({"check": "dataset_version_inventory", "status": "missing"})
    if not _has_items(preflight.get("activeIndexPointers")):
        findings.append({"check": "object_index_pointer", "status": "missing"})
    findings.extend(_missing_runtime_evidence(preflight.get("highWatermarks")))
    return findings


def require_restore_resume_ready(
    restore_id: str,
    findings: Sequence[Mapping[str, object]],
) -> None:
    """Fail closed when post-restore validation findings still exist."""
    if findings:
        raise ConflictDetected(
            "post-restore validation must pass before publisher resume",
            details={"restore_id": restore_id, "findings": list(findings)},
        )


def _missing_runtime_evidence(high_watermarks: object) -> list[dict[str, object]]:
    if not isinstance(high_watermarks, Mapping):
        return [
            {"check": "actionRuns", "status": "missing"},
            {"check": "materializationRuns", "status": "missing"},
        ]
    findings: list[dict[str, object]] = []
    high_watermark_map = cast(Mapping[str, object], high_watermarks)
    for key in ("actionRuns", "materializationRuns"):
        if _watermark_count(high_watermark_map, key) == 0:
            findings.append({"check": key, "status": "missing"})
    return findings


def _has_items(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return False
    items = cast(Sequence[object], value)
    return len(items) > 0


def _watermark_count(high_watermarks: Mapping[str, object], name: str) -> int:
    table = high_watermarks.get(name)
    if isinstance(table, Mapping):
        table_map = cast(Mapping[str, object], table)
        count = table_map.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            return int(count)
    if isinstance(table, int) and not isinstance(table, bool):
        return table
    return 0
