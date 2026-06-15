from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports import ComputeAdapter
from foundry_lite.application.ports.compute_adapter import SqlTransformPlan
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter, FakeComputeAdapter


@pytest.fixture(params=[DuckDBComputeAdapter, FakeComputeAdapter])
def adapter(request: pytest.FixtureRequest) -> ComputeAdapter:
    adapter_type = request.param
    return adapter_type()


def test_compute_adapter_contract_csv_parquet_preview_inspect_and_rows(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,status,amount\nO-1,PENDING,10\nO-2,APPROVED,20\n", encoding="utf-8")
    parquet_path = tmp_path / "orders.parquet"

    adapter.csv_to_parquet(csv_path, parquet_path)
    preview = adapter.preview_parquet(parquet_path, limit=1)
    rows = adapter.rows_from_parquet(parquet_path)
    stats = adapter.inspect_parquet(parquet_path, ["order_id"])

    assert preview == [{"order_id": "O-1", "status": "PENDING", "amount": 10}]
    assert [row["order_id"] for row in rows] == ["O-1", "O-2"]
    assert stats.row_count == 2
    assert stats.schema_json["primary_key"] == ["order_id"]
    assert stats.schema_json["columns"][0]["nullable"] is False


def test_csv_primary_key_preserves_leading_zeroes(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "leading_zero_orders.csv"
    csv_path.write_text("order_id,amount\n00123,100\n", encoding="utf-8")
    parquet_path = tmp_path / "leading_zero_orders.parquet"

    adapter.csv_to_parquet(csv_path, parquet_path)
    rows = adapter.rows_from_parquet(parquet_path)
    stats = adapter.inspect_parquet(parquet_path, ["order_id"])

    assert rows == [{"order_id": "00123", "amount": 100}]
    assert stats.schema_json["columns"][0] == {"name": "order_id", "type": "string", "nullable": False}


def test_compute_adapter_contract_rows_to_parquet_and_health_checks(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "rows.parquet"
    adapter.rows_to_parquet(
        [
            {"order_id": "O-1", "status": "PENDING"},
            {"order_id": "O-1", "status": ""},
            {"order_id": None, "status": "REVIEW"},
        ],
        parquet_path,
        ["order_id", "status"],
    )

    row_count = adapter.inspect_parquet(parquet_path, ["order_id"]).row_count
    min_check = adapter.execute_check(parquet_path, row_count, {"type": "row_count_min", "min": 4})
    not_null = adapter.execute_check(parquet_path, row_count, {"type": "not_null", "columns": ["order_id"]})
    unique = adapter.execute_check(parquet_path, row_count, {"type": "unique", "column": "order_id"})
    custom = adapter.execute_check(parquet_path, row_count, {"type": "custom"})

    assert min_check["status"] == "failed"
    assert not_null == {"check": "not_null", "status": "failed", "failures": {"order_id": 1}}
    assert unique["status"] == "failed"
    assert unique["duplicate_groups"] == 1
    assert custom["status"] == "passed"


def test_compute_adapter_contract_sql_transform_and_unresolved_inputs(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    target_path = tmp_path / "target.parquet"
    adapter.rows_to_parquet(
        [
            {"order_id": "O-1", "amount": 10},
            {"order_id": "O-2", "amount": 20},
        ],
        source_path,
        ["order_id", "amount"],
    )

    adapter.execute_transform(
        SqlTransformPlan(
            sql_template="select order_id, amount * 2 as doubled from {{ input('raw.orders') }}",
            input_paths_by_ref={"raw.orders": source_path},
            target_path=target_path,
        )
    )

    assert adapter.rows_from_parquet(target_path) == [
        {"order_id": "O-1", "doubled": 20},
        {"order_id": "O-2", "doubled": 40},
    ]
    with pytest.raises(ValidationFailed):
        adapter.execute_transform(
            SqlTransformPlan(
                sql_template="select * from {{ input('missing.dataset') }}",
                input_paths_by_ref={"raw.orders": source_path},
                target_path=tmp_path / "missing.parquet",
            )
        )


def test_compute_adapter_contract_unsupported_transform_plan_raises_typed_error(
    adapter: ComputeAdapter,
) -> None:
    """Future TransformPlan kinds must raise a typed ValidationFailed rather
    than silently degrade. This pins the Sprint 02A boundary semantic:
    'adapter failures are typed; no silent vendor coercion'."""
    from dataclasses import dataclass
    from pathlib import Path as _Path

    @dataclass(frozen=True)
    class _UnsupportedPlan:
        target_path: _Path

    with pytest.raises(ValidationFailed):
        adapter.execute_transform(_UnsupportedPlan(target_path=_Path("/tmp/never.parquet")))  # type: ignore[arg-type]
