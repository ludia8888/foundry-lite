from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from foundry_lite.application.ports import ComputeAdapter
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.compute_adapter import (
    PythonTransformPlan,
    SqlTransformPlan,
    TransformExecutionResult,
)
from foundry_lite.application.ports.dataset_aggregation import DatasetAggregationPlan
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter, FakeComputeAdapter
from foundry_lite.infrastructure.adapters import compute as compute_module


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
    bounded = adapter.rows_from_parquet_bounded(
        parquet_path,
        max_rows=2,
        max_decoded_bytes=10_000,
    )
    stats = adapter.inspect_parquet(parquet_path, ["order_id"])

    assert preview == [{"order_id": "O-1", "status": "PENDING", "amount": 10}]
    assert [row["order_id"] for row in rows] == ["O-1", "O-2"]
    assert bounded.rows == tuple(rows)
    assert bounded.decoded_byte_count > 0
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
            {"order_id": "O-1", "status": "REJECTED"},
            {"order_id": None, "status": "REVIEW"},
        ],
        parquet_path,
        ["order_id", "status"],
    )

    row_count = adapter.inspect_parquet(parquet_path, ["order_id"]).row_count
    min_check = adapter.execute_check(parquet_path, row_count, {"type": "row_count_min", "min": 4})
    not_null = adapter.execute_check(parquet_path, row_count, {"type": "not_null", "columns": ["order_id"]})
    unique = adapter.execute_check(parquet_path, row_count, {"type": "unique", "column": "order_id"})
    unique_tuple = adapter.execute_check(
        parquet_path,
        row_count,
        {"type": "unique_tuple", "columns": ["order_id", "status"]},
    )
    accepted_values = adapter.execute_check(
        parquet_path,
        row_count,
        {"type": "accepted_values", "column": "status", "values": ["PENDING", "APPROVED"]},
    )
    assert min_check["status"] == "failed"
    assert not_null == {"check": "not_null", "status": "failed", "failures": {"order_id": 1}}
    assert unique["status"] == "failed"
    assert unique["duplicate_groups"] == 1
    assert unique_tuple["status"] == "passed"
    assert accepted_values["status"] == "failed"
    assert accepted_values["invalid_count"] == 2
    assert "REVIEW" in accepted_values["sample_invalid_values"]
    with pytest.raises(ValidationFailed, match="unsupported dataset quality check type"):
        adapter.execute_check(parquet_path, row_count, {"type": "custom"})


def test_compute_adapter_contract_preserves_null_only_column_type(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "null-only.parquet"
    adapter.rows_to_parquet([{"id": "A", "note": None}], parquet_path, ["id", "note"])

    stats = adapter.inspect_parquet(parquet_path, ["id"])

    assert stats.schema_json["columns"][1] == {"name": "note", "type": "null", "nullable": True}


def test_compute_adapter_contract_applies_declared_type_to_null_only_column(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "declared-null-only.parquet"
    adapter.rows_to_parquet(
        [{"id": "A", "lag_seconds": None}],
        parquet_path,
        ["id", "lag_seconds"],
        field_types={"id": "string", "lag_seconds": "float64"},
    )

    stats = adapter.inspect_parquet(parquet_path, ["id"])

    assert stats.schema_json["columns"] == [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "lag_seconds", "type": "float", "nullable": True},
    ]


def test_compute_adapter_contract_rows_to_parquet_preserves_nested_json_values(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "nested-rows.parquet"
    rows = [
        {
            "order_id": "O-1",
            "amount": 10,
            "analysis": {"category": "payment", "risk": 2},
            "evidence": {"tokens": {"input": 23, "output": 7}, "mediaIds": []},
            "labels": ["finance", "review"],
            "emptyLabels": [],
            "emptyContext": {},
        },
        {
            "order_id": "O-2",
            "amount": 20.5,
            "analysis": {"category": "shipping", "risk": 1},
            "evidence": {"tokens": {"input": 17, "output": 5}, "mediaIds": ["media-version-1"]},
            "labels": [],
            "emptyLabels": [],
            "emptyContext": {},
        },
    ]

    adapter.rows_to_parquet(
        rows,
        parquet_path,
        ["order_id", "amount", "analysis", "evidence", "labels", "emptyLabels", "emptyContext"],
    )

    assert adapter.rows_from_parquet(parquet_path) == rows
    assert adapter.preview_parquet(parquet_path, limit=1) == rows[:1]
    assert not parquet_path.with_suffix(".csv").exists()


def test_compute_adapter_contract_rows_to_parquet_rejects_incompatible_nested_shapes(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "invalid-nested-rows.parquet"

    with pytest.raises(ValidationFailed, match="typed parquet"):
        adapter.rows_to_parquet(
            [{"analysis": {"risk": 2}}, {"analysis": "not-an-object"}],
            parquet_path,
            ["analysis"],
        )

    assert not parquet_path.exists()


def test_duckdb_rows_to_parquet_validates_shape_and_cleans_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DuckDBComputeAdapter()
    scalar_path = tmp_path / "typed-scalars.parquet"
    large_integer = 2**80
    adapter.rows_to_parquet(
        [{"none": None, "approved": True, "large": large_integer}],
        scalar_path,
        ["none", "approved", "large"],
    )
    assert adapter.rows_from_parquet(scalar_path) == [{"none": None, "approved": True, "large": large_integer}]

    invalid_cases = (
        ([{"value": 1}], []),
        ([{"value": 1}], ["value", "value"]),
        ([{"value": 1}], [""]),
        ([["not-a-row"]], ["value"]),
        ([{"value": 10**39}], ["value"]),
    )
    for rows, fieldnames in invalid_cases:
        with pytest.raises(ValidationFailed, match="typed parquet"):
            adapter.rows_to_parquet(rows, tmp_path / "invalid.parquet", fieldnames)  # type: ignore[arg-type]

    partial_path = tmp_path / "partial.parquet"

    def fail_write(*_args: object, **_kwargs: object) -> None:
        partial_path.write_bytes(b"partial")
        raise compute_module.pa.ArrowInvalid("injected write failure")

    monkeypatch.setattr(compute_module.pq, "write_table", fail_write)
    with pytest.raises(ValidationFailed, match="typed parquet") as captured:
        adapter.rows_to_parquet([{"value": 1}], partial_path, ["value"])
    assert captured.value.details["errorType"] == "ArrowInvalid"
    assert not partial_path.exists()


def test_duckdb_bounded_parquet_read_rejects_highly_compressed_decoded_payload(
    tmp_path: Path,
) -> None:
    adapter = DuckDBComputeAdapter()
    parquet_path = tmp_path / "compressed.parquet"
    adapter.rows_to_parquet(
        rows := [{"payload": "x" * 100_000} for _ in range(20)],
        parquet_path,
        ["payload"],
    )
    metadata = pq.ParquetFile(parquet_path).metadata
    metadata_bytes = (
        sum(
            metadata.row_group(group).column(column).total_uncompressed_size
            for group in range(metadata.num_row_groups)
            for column in range(metadata.num_columns)
        )
        + metadata.num_rows * metadata.num_columns * 16
    )
    decoded_bytes = sum(len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()) for row in rows)
    decoded_limit = (metadata_bytes + decoded_bytes) // 2
    assert metadata_bytes < decoded_limit < decoded_bytes

    with pytest.raises(ValidationFailed, match="read bound") as raised:
        adapter.rows_from_parquet_bounded(
            parquet_path,
            max_rows=100,
            max_decoded_bytes=decoded_limit,
        )

    assert raised.value.details["limitKind"] == "decoded_bytes"
    assert raised.value.details["actual"] > raised.value.details["maximum"]
    assert raised.value.details["compressedByteCount"] < raised.value.details["actual"]


def test_duckdb_bounded_parquet_read_rejects_nested_values_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parquet_path = tmp_path / "nested.parquet"
    pq.write_table(
        pa.Table.from_pylist([{"payload": ["x" * 1_000 for _ in range(1_000)]}]),
        parquet_path,
        use_dictionary=True,
        compression="zstd",
    )
    monkeypatch.setattr(
        compute_module,
        "_tabular_row",
        lambda _value: pytest.fail("nested values must be rejected before Python row materialization"),
    )

    with pytest.raises(ValidationFailed, match="flat scalar columns") as raised:
        DuckDBComputeAdapter().rows_from_parquet_bounded(
            parquet_path,
            max_rows=10,
            max_decoded_bytes=256 * 1024 * 1024,
        )

    assert raised.value.details["limitKind"] == "nested_values"
    assert raised.value.details["columns"] == ["payload"]


def test_duckdb_bounded_parquet_read_allows_pinned_geojson_geometry(
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "geojson.parquet"
    row = {
        "asset_id": "A-1",
        "geometry": {"type": "Point", "coordinates": [127.0, 37.5]},
    }
    pq.write_table(pa.Table.from_pylist([row]), parquet_path)

    read = DuckDBComputeAdapter().rows_from_parquet_bounded(
        parquet_path,
        max_rows=10,
        max_decoded_bytes=1024 * 1024,
        allowed_nested_columns=("geometry",),
    )

    assert read.rows == (row,)
    assert 0 < read.decoded_byte_count <= 1024 * 1024


def test_duckdb_bounded_geojson_rejects_metadata_limit_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parquet_path = tmp_path / "large-geojson.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{"geometry": {"type": "LineString", "coordinates": [[float(index), 0.0] for index in range(1_000)]}}]
        ),
        parquet_path,
        compression="zstd",
    )
    monkeypatch.setattr(
        compute_module,
        "_tabular_row",
        lambda _value: pytest.fail("over-limit GeoJSON must fail before Python row materialization"),
    )

    with pytest.raises(ValidationFailed, match="read bound") as raised:
        DuckDBComputeAdapter().rows_from_parquet_bounded(
            parquet_path,
            max_rows=10,
            max_decoded_bytes=1024,
            allowed_nested_columns=("geometry",),
        )

    assert raised.value.details["limitKind"] == "decoded_bytes"
    assert raised.value.details["maximum"] == 1024


def test_compute_adapter_contract_detects_duplicate_composite_tuple(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "composite.parquet"
    adapter.rows_to_parquet(
        [
            {"order_id": "O-1", "line_id": "1", "amount": 10},
            {"order_id": "O-1", "line_id": "2", "amount": 20},
            {"order_id": "O-1", "line_id": "1", "amount": 30},
        ],
        parquet_path,
        ["order_id", "line_id", "amount"],
    )

    row_count = adapter.inspect_parquet(parquet_path, ["order_id", "line_id"]).row_count
    result = adapter.execute_check(
        parquet_path,
        row_count,
        {"type": "unique_tuple", "columns": ["order_id", "line_id"]},
    )

    assert result["status"] == "failed"
    assert result["duplicate_groups"] == 1


def test_compute_adapter_contract_aggregates_parquet_with_filters_and_group_limit(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "orders-a.parquet"
    second_path = tmp_path / "orders-b.parquet"
    adapter.rows_to_parquet(
        [
            {"order_id": "O-1", "status": "PENDING", "region": "APAC", "amount": 100},
            {"order_id": "O-2", "status": "APPROVED", "region": "APAC", "amount": 50},
        ],
        first_path,
        ["order_id", "status", "region", "amount"],
    )
    adapter.rows_to_parquet(
        [{"order_id": "O-3", "status": "PENDING", "region": "EMEA", "amount": 200}],
        second_path,
        ["order_id", "status", "region", "amount"],
    )

    result = adapter.aggregate_parquet(
        [first_path, second_path],
        DatasetAggregationPlan(
            group_by=("region",),
            filters=({"column": "status", "operator": "eq", "value": "PENDING"},),
            metrics=(
                {"function": "count", "name": "count", "property": None},
                {"function": "sum", "name": "amount", "property": "amount"},
            ),
            group_limit=10,
        ),
    )

    assert result["rowCount"] == 3
    assert result["filteredRowCount"] == 2
    assert result["groups"] == [
        {"key": {"region": "APAC"}, "metrics": {"count": 1, "amount": 100.0}},
        {"key": {"region": "EMEA"}, "metrics": {"count": 1, "amount": 200.0}},
    ]

    with pytest.raises(ValidationFailed, match="exceeds the group limit"):
        adapter.aggregate_parquet(
            [first_path, second_path],
            DatasetAggregationPlan(
                group_by=("order_id",),
                filters=(),
                metrics=({"function": "count", "name": "count", "property": None},),
                group_limit=2,
            ),
        )


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


def test_compute_adapter_contract_sql_transform_reads_multiple_input_files(
    adapter: ComputeAdapter,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "source-a.parquet"
    second_path = tmp_path / "source-b.parquet"
    target_path = tmp_path / "target.parquet"
    adapter.rows_to_parquet([{"order_id": "O-1", "amount": 10}], first_path, ["order_id", "amount"])
    adapter.rows_to_parquet([{"order_id": "O-2", "amount": 20}], second_path, ["order_id", "amount"])

    adapter.execute_transform(
        SqlTransformPlan(
            sql_template="select order_id, amount from {{ input('raw.orders') }} order by order_id",
            input_paths_by_ref={"raw.orders": (first_path, second_path)},
            target_path=target_path,
        )
    )

    assert adapter.rows_from_parquet(target_path) == [
        {"order_id": "O-1", "amount": 10},
        {"order_id": "O-2", "amount": 20},
    ]


def test_duckdb_sql_transform_cannot_read_files_outside_declared_inputs(tmp_path: Path) -> None:
    """Transform SQL is user-supplied via the HTTP API, so DuckDB must run it with filesystem
    access disabled. A replacement scan (bare path in FROM) and glob() both bypass the guard
    regex, so this pins the runtime sandbox that actually stops cross-tenant file exfiltration."""
    import duckdb

    adapter = DuckDBComputeAdapter()
    declared = tmp_path / "declared.parquet"
    adapter.rows_to_parquet([{"order_id": "O-1", "amount": 10}], declared, ["order_id", "amount"])
    victim = tmp_path / "other-tenant.parquet"
    adapter.rows_to_parquet([{"secret": "OTHER_TENANT_ROW"}], victim, ["secret"])

    for sql in (f"select * from '{victim}'", "select count(*) from glob('/etc/*')"):
        target_path = tmp_path / "exfiltrated.parquet"
        with pytest.raises(duckdb.Error):
            adapter.execute_transform(
                SqlTransformPlan(sql_template=sql, input_paths_by_ref={}, target_path=target_path)
            )
        assert not target_path.exists()


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


def test_duckdb_compute_adapter_python_transform_fails_closed_without_isolation_adapter(tmp_path: Path) -> None:
    adapter = DuckDBComputeAdapter()
    marker = tmp_path / "api-process-marker"
    plan = PythonTransformPlan(
        entrypoint=str(tmp_path / "unsafe.py"),
        source_code=f"open({str(marker)!r}, 'w').write('executed')\n",
        function_name=None,
        input_refs_by_alias={},
        input_paths_by_ref={},
        output_dataset_ref="clean.orders",
        target_path=tmp_path / "target.parquet",
    )

    with pytest.raises(AdapterError) as captured:
        adapter.execute_transform(plan)

    assert captured.value.failure.details["codeExecution"] == {
        "failureType": "runtime_unavailable",
        "executionAttempted": False,
    }
    assert not marker.exists()
    assert not plan.target_path.exists()


def test_duckdb_compute_adapter_delegates_python_plan_to_code_execution_boundary(tmp_path: Path) -> None:
    delegated: list[PythonTransformPlan] = []

    class _RecordingCodeExecutionAdapter:
        def execute_python_transform(self, plan: PythonTransformPlan) -> TransformExecutionResult:
            delegated.append(plan)
            return TransformExecutionResult()

    adapter = DuckDBComputeAdapter(code_execution_adapter=_RecordingCodeExecutionAdapter())  # type: ignore[arg-type]
    plan = PythonTransformPlan(
        entrypoint=str(tmp_path / "delegated.py"),
        source_code="def compute():\n    return [{'ok': True}]\n",
        function_name="compute",
        input_refs_by_alias={},
        input_paths_by_ref={},
        output_dataset_ref="clean.orders",
        target_path=tmp_path / "delegated.parquet",
    )

    assert adapter.execute_transform(plan) == TransformExecutionResult()
    assert delegated == [plan]


def test_duckdb_compute_adapter_helper_edges() -> None:
    with pytest.raises(ValidationFailed, match="row must be a mapping"):
        compute_module._tabular_row(["bad"])
    with pytest.raises(ValidationFailed, match="unique tuple check requires columns"):
        compute_module._check_columns({"type": "unique_tuple", "columns": "order_id"})
    with pytest.raises(ValidationFailed, match="unique tuple check requires string columns"):
        compute_module._check_columns({"type": "unique_tuple", "columns": ["order_id", 1]})
    with pytest.raises(ValidationFailed, match="accepted values check requires values"):
        compute_module._check_values({"type": "accepted_values", "values": []})
    with pytest.raises(ValidationFailed, match="accepted values check requires scalar values"):
        compute_module._check_values({"type": "accepted_values", "values": [["nested"]]})
