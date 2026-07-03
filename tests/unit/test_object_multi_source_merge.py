"""Direct tests for the multi-datasource column-wise merge used by reindexes."""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports import DatasetVersionRow, TabularRow
from foundry_lite.application.services.object_store.indexing_multi_source import (
    multi_source_version_id,
    multi_source_version_ids_by_name,
    read_merged_source_rows,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexMultiSourcePlan,
    ObjectIndexSourceSegment,
)
from foundry_lite.domain.errors import ValidationFailed


def _version(version_id: str) -> DatasetVersionRow:
    return {"id": version_id}  # type: ignore[typeddict-item]


def _plan() -> ObjectIndexMultiSourcePlan:
    return ObjectIndexMultiSourcePlan(
        primary_key_column="order_id",
        segments=(
            ObjectIndexSourceSegment(
                name="erp",
                dataset_ref="clean.orders",
                primary_key_column="order_id",
                property_columns=("amount", "region"),
                dataset_version=_version("dsv_erp"),
            ),
            ObjectIndexSourceSegment(
                name="finance",
                dataset_ref="clean.order_finance",
                primary_key_column="fin_order_id",
                property_columns=("margin",),
                dataset_version=_version("dsv_fin"),
            ),
        ),
    )


def _reader(rows_by_version: dict[str, list[TabularRow]]):
    def rows_from_parquet(path: Path) -> list[TabularRow]:
        return rows_by_version[path.name]

    return rows_from_parquet


def _version_file_path(version: DatasetVersionRow) -> Path:
    return Path(str(version["id"]))


def test_merge_unions_primary_keys_and_nulls_missing_segments() -> None:
    rows = read_merged_source_rows(
        "Order",
        _plan(),
        version_file_path=_version_file_path,
        rows_from_parquet=_reader(
            {
                "dsv_erp": [
                    {"order_id": "O-1", "amount": 100.0, "region": "NA"},
                    {"order_id": "O-2", "amount": 50.0, "region": "EU"},
                ],
                "dsv_fin": [
                    {"fin_order_id": "O-1", "margin": 20.0},
                    {"fin_order_id": "O-3", "margin": 7.5},
                ],
            }
        ),
    )

    by_pk = {str(row["order_id"]): row for row in rows}
    assert list(by_pk) == ["O-1", "O-2", "O-3"]
    assert by_pk["O-1"]["amount"] == 100.0
    assert by_pk["O-1"]["margin"] == 20.0
    # PK only in erp: the finance property column is explicitly null.
    assert by_pk["O-2"]["margin"] is None
    # PK only in finance: the object exists (union) with erp columns null.
    assert by_pk["O-3"]["amount"] is None
    assert by_pk["O-3"]["region"] is None
    assert by_pk["O-3"]["margin"] == 7.5


def test_property_columns_resolve_from_their_own_segment_on_name_collisions() -> None:
    # Both datasets expose a "margin" column but the property maps to the
    # finance segment: erp's value must never leak through the merge.
    rows = read_merged_source_rows(
        "Order",
        _plan(),
        version_file_path=_version_file_path,
        rows_from_parquet=_reader(
            {
                "dsv_erp": [{"order_id": "O-1", "amount": 100.0, "region": "NA", "margin": 999.0}],
                "dsv_fin": [{"fin_order_id": "O-1", "margin": 20.0}],
            }
        ),
    )

    assert rows[0]["margin"] == 20.0


def test_duplicate_primary_keys_within_one_segment_are_rejected() -> None:
    with pytest.raises(ValidationFailed, match="duplicate primary keys") as exc_info:
        read_merged_source_rows(
            "Order",
            _plan(),
            version_file_path=_version_file_path,
            rows_from_parquet=_reader(
                {
                    "dsv_erp": [
                        {"order_id": "O-1", "amount": 100.0, "region": "NA"},
                        {"order_id": "O-1", "amount": 200.0, "region": "EU"},
                    ],
                    "dsv_fin": [],
                }
            ),
        )

    assert exc_info.value.details["sourceDatasetVersionId"] == "dsv_erp"


def test_null_primary_key_in_any_segment_is_rejected() -> None:
    with pytest.raises(ValidationFailed, match="object primary key cannot be null"):
        read_merged_source_rows(
            "Order",
            _plan(),
            version_file_path=_version_file_path,
            rows_from_parquet=_reader(
                {
                    "dsv_erp": [{"order_id": "O-1", "amount": 100.0, "region": "NA"}],
                    "dsv_fin": [{"fin_order_id": None, "margin": 20.0}],
                }
            ),
        )


def test_composite_version_id_joins_segments_in_declaration_order() -> None:
    plan = _plan()

    assert multi_source_version_id(plan) == "dsv_erp+dsv_fin"
    assert multi_source_version_ids_by_name(plan) == {"erp": "dsv_erp", "finance": "dsv_fin"}
