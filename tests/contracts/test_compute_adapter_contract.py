from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports import ComputeAdapter
from foundry_lite.application.ports.compute_adapter import PythonTransformPlan, SqlTransformPlan
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


def test_duckdb_compute_adapter_python_transform_returned_rows_and_fail_closed_edges(tmp_path: Path) -> None:
    adapter = DuckDBComputeAdapter()
    source_path = tmp_path / "source.parquet"
    target_path = tmp_path / "python-target.parquet"
    adapter.rows_to_parquet([{"order_id": "O-1", "amount": 10}], source_path, ["order_id", "amount"])

    result = adapter.execute_transform(
        PythonTransformPlan(
            entrypoint=str(tmp_path / "return_rows_transform.py"),
            source_code=(
                "def compute(orders):\n"
                "    return [\n"
                "        {'order_id': row['order_id'], 'amount': row['amount'] * 3}\n"
                "        for row in orders.read_rows()\n"
                "    ]\n"
            ),
            function_name="compute",
            input_refs_by_alias={"orders": "raw.orders"},
            input_paths_by_ref={"raw.orders": source_path},
            output_dataset_ref="clean.orders",
            target_path=target_path,
        )
    )

    assert result.dead_letters == ()
    assert adapter.rows_from_parquet(target_path) == [{"order_id": "O-1", "amount": 30}]
    with pytest.raises(ValidationFailed, match="function not found"):
        adapter.execute_transform(
            PythonTransformPlan(
                entrypoint=str(tmp_path / "missing_function.py"),
                source_code="def compute():\n    return [{'ok': True}]\n",
                function_name="missing",
                input_refs_by_alias={},
                input_paths_by_ref={},
                output_dataset_ref="clean.orders",
                target_path=tmp_path / "missing-function.parquet",
            )
        )
    with pytest.raises(ValidationFailed, match="did not write output"):
        adapter.execute_transform(
            PythonTransformPlan(
                entrypoint=str(tmp_path / "no_output.py"),
                source_code="def compute():\n    return None\n",
                function_name="compute",
                input_refs_by_alias={},
                input_paths_by_ref={},
                output_dataset_ref="clean.orders",
                target_path=tmp_path / "no-output.parquet",
            )
        )
    with pytest.raises(ValidationFailed, match="return value must be a sequence"):
        adapter.execute_transform(
            PythonTransformPlan(
                entrypoint=str(tmp_path / "bad_return.py"),
                source_code="def compute():\n    return 'bad'\n",
                function_name="compute",
                input_refs_by_alias={},
                input_paths_by_ref={},
                output_dataset_ref="clean.orders",
                target_path=tmp_path / "bad-return.parquet",
            )
        )
    with pytest.raises(ValidationFailed, match="return rows must be mappings"):
        adapter.execute_transform(
            PythonTransformPlan(
                entrypoint=str(tmp_path / "bad_rows.py"),
                source_code="def compute():\n    return ['bad']\n",
                function_name="compute",
                input_refs_by_alias={},
                input_paths_by_ref={},
                output_dataset_ref="clean.orders",
                target_path=tmp_path / "bad-rows.parquet",
            )
        )


def test_duckdb_compute_adapter_python_transform_output_aliases_and_helper_edges(tmp_path: Path) -> None:
    adapter = DuckDBComputeAdapter()
    first_path = tmp_path / "first.parquet"
    second_path = tmp_path / "second.parquet"
    output_path = tmp_path / "output-alias.parquet"
    out_path = tmp_path / "out-alias.parquet"
    adapter.rows_to_parquet([{"order_id": "O-1"}], first_path, ["order_id"])
    adapter.rows_to_parquet([{"order_id": "O-2"}], second_path, ["order_id"])

    adapter.execute_transform(
        PythonTransformPlan(
            entrypoint=str(tmp_path / "output_alias.py"),
            source_code=(
                "def compute(orders, output):\n"
                "    output.write_rows([{'order_id': row['order_id']} for row in orders.read_rows()])\n"
            ),
            function_name=None,
            input_refs_by_alias={"orders": "raw.orders"},
            input_paths_by_ref={"raw.orders": (first_path, second_path)},
            output_dataset_ref="clean.orders",
            target_path=output_path,
        )
    )
    adapter.execute_transform(
        PythonTransformPlan(
            entrypoint=str(tmp_path / "out_alias.py"),
            source_code="def compute(out):\n    out.write_rows([{'ok': True}])\n",
            function_name=None,
            input_refs_by_alias={},
            input_paths_by_ref={},
            output_dataset_ref="clean.orders",
            target_path=out_path,
        )
    )

    assert adapter.rows_from_parquet(output_path) == [{"order_id": "O-1"}, {"order_id": "O-2"}]
    assert adapter.rows_from_parquet(out_path) == [{"ok": True}]
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
