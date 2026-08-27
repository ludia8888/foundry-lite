"""Pure materialization result and watermark payload helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import cast

from foundry_lite.application.ports import DatasetRow, DatasetVersionRow, RuntimeRow
from foundry_lite.application.ports.materialization_repository import MaterializationRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.materialization_types import MaterializationRunPlan, MaterializationSpec
from foundry_lite.application.services.materialization_watermarks import materialization_platform_watermark


def materialization_with_spec(row: MaterializationRow, spec: MaterializationSpec) -> MaterializationRow:
    """Overlay the active declaration while preserving the stored run identity."""
    updated = dict(row)
    updated["materialization_type"] = spec.materialization_type
    updated["source_ref"] = spec.source.copy()
    updated["target_ref"] = spec.target.copy()
    return cast(MaterializationRow, updated)


def materialization_transaction_metadata(plan: MaterializationRunPlan) -> dict[str, object]:
    reopen = plan.watermark.get("lateDataReopen")
    return {
        "platformWatermark": materialization_platform_watermark(plan),
        "materializationDetail": {
            "apiName": plan.api_name,
            "materializationType": plan.materialization_type,
            "watermark": dict(plan.watermark),
            "reopen": reopen if isinstance(reopen, Mapping) else {"isReopened": False},
        },
    }


def run_watermark(run: RuntimeRow) -> Mapping[str, object]:
    watermark = run.get("object_store_watermark") or run.get("source_cursor")
    return watermark if isinstance(watermark, Mapping) else {}


def materialization_rows_hash(rows: Sequence[Mapping[str, object]]) -> str:
    payload = json.dumps([dict(row) for row in rows], sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def commit_result_for_existing_materialization(
    dataset: DatasetRow,
    version: DatasetVersionRow,
    schema_hash: str,
) -> CommitResult:
    return CommitResult(
        dataset_id=dataset["id"],
        dataset_ref=f"{dataset['namespace']}.{dataset['name']}",
        transaction_id=version["transaction_id"],
        version_id=version["id"],
        version_number=version["version_number"],
        row_count=version["row_count"],
        manifest_uri=version["manifest_uri"],
        schema_hash=schema_hash,
    )
