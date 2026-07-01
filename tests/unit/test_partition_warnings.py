from __future__ import annotations

from pathlib import Path
from typing import cast

from foundry_lite.application.ports import DatasetRow, DatasetStagedFile
from foundry_lite.application.primitives import StagedFileStats
from foundry_lite.application.services.dataset.partition_warnings import partition_layout_warnings
from foundry_lite.application.services.dataset.transaction_models import (
    DatasetCommitTarget,
    DatasetFinalizationCheck,
    DatasetFinalizationRequest,
)


def test_partition_layout_warns_when_declared_partition_is_nearly_unique_per_row() -> None:
    request = _request(
        partition_spec=["customer_id"],
        staged_files=tuple(
            DatasetStagedFile(Path(f"part-{index}.parquet"), row_count=1, partition_values={"customer_id": index})
            for index in range(5)
        ),
    )

    warnings = partition_layout_warnings(request, _validation(row_count=5))

    assert warnings == [
        {
            "code": "high_cardinality_partition",
            "severity": "warning",
            "datasetRef": "raw.orders",
            "column": "customer_id",
            "partitionSpec": ["customer_id"],
            "uniquePartitionValueCount": 5,
            "rowCount": 5,
            "fileCount": 5,
            "uniqueToRowRatio": 1.0,
            "recommendation": "Use lower-cardinality partition columns or rely on sort/order bounds for pruning.",
        }
    ]


def test_partition_layout_does_not_warn_for_low_cardinality_partition() -> None:
    request = _request(
        partition_spec=["bucket"],
        staged_files=(
            DatasetStagedFile(Path("part-a.parquet"), row_count=3, partition_values={"bucket": "a"}),
            DatasetStagedFile(Path("part-b.parquet"), row_count=3, partition_values={"bucket": "b"}),
        ),
    )

    assert partition_layout_warnings(request, _validation(row_count=6)) == []


def test_partition_layout_ignores_missing_partition_spec_and_counts_zero_value() -> None:
    no_spec = _request(
        partition_spec=[],
        staged_files=(DatasetStagedFile(Path("part.parquet"), row_count=1, partition_values={"bucket": 0}),),
    )
    with_zero_values = _request(
        partition_spec=["bucket"],
        staged_files=tuple(
            DatasetStagedFile(Path(f"part-{index}.parquet"), row_count=1, partition_values={"bucket": index})
            for index in range(5)
        ),
    )

    assert partition_layout_warnings(no_spec, _validation(row_count=1)) == []
    assert partition_layout_warnings(with_zero_values, _validation(row_count=5))[0]["uniquePartitionValueCount"] == 5


def _request(
    *,
    partition_spec: list[str],
    staged_files: tuple[DatasetStagedFile, ...],
) -> DatasetFinalizationRequest:
    dataset = cast(
        DatasetRow,
        {
            "id": "ds_orders",
            "tenant_id": "tenant_demo",
            "namespace": "raw",
            "name": "orders",
            "description": None,
            "storage_kind": "parquet_manifest",
            "storage_uri": "local://orders",
            "owner_team": None,
            "classification": None,
            "status": "active",
            "primary_key": ["id"],
            "partition_spec": partition_spec,
            "sort_order": [],
            "target_file_size_bytes": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    return DatasetFinalizationRequest(
        target=DatasetCommitTarget.from_row(dataset),
        transaction_id="dstx_orders",
        staged_parquet=Path("validation.parquet"),
        staged_files=staged_files,
        run_id="sync_orders",
        audit_action="dataset_commit",
        outbox_event_type="dataset.version.committed",
        extra_checks=[],
        transaction_metadata={},
    )


def _validation(*, row_count: int) -> DatasetFinalizationCheck:
    return DatasetFinalizationCheck(
        branch="main",
        stats=StagedFileStats(
            parquet_path=Path("validation.parquet"),
            row_count=row_count,
            byte_size=10,
            content_hash="sha256:content",
            schema_json={"fields": []},
            schema_hash="sha256:schema",
        ),
        checked_manifest_hash="sha256:manifest",
        staged_files_hash="sha256:files",
        schema_version_id="dss_1",
        schema_version=1,
    )
