from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.compute_adapter import SqlTransformPlan
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter, SparkComputeAdapter
from foundry_lite.infrastructure.adapters.spark_compute import _build_local_spark_session
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.fixture(scope="session")
def spark_session() -> Iterator[Any]:
    session = _build_local_spark_session()
    session.sparkContext.setLogLevel("ERROR")
    try:
        yield session
    finally:
        session.stop()


def _adapter(spark_session: Any) -> SparkComputeAdapter:
    return SparkComputeAdapter(session=spark_session)


def _orders_csv(tmp_path: Path) -> Path:
    path = tmp_path / f"orders-{uuid4().hex}.csv"
    path.write_text("id,amount\nO-1,100\nO-2,200\nO-3,300\n", encoding="utf-8")
    return path


def test_spark_compute_adapter_contract_parity(spark_session: Any, tmp_path: Path) -> None:
    # adapter-contract + normal-path: CSV -> parquet -> preview/inspect/check, all on the
    # Spark profile, reading back through the shared local reader.
    adapter = _adapter(spark_session)
    parquet = tmp_path / "part-00000.parquet"
    adapter.csv_to_parquet(_orders_csv(tmp_path), parquet)

    assert parquet.is_file()
    preview = adapter.preview_parquet(parquet, limit=10)
    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100), ("O-2", 200), ("O-3", 300)]
    stats = adapter.inspect_parquet(parquet, ["id"])
    assert stats.row_count == 3
    assert adapter.execute_check(parquet, stats.row_count, {"type": "row_count_min", "min": 1})["status"] == "passed"
    assert adapter.failure_contract().adapter_profile == "spark"


def test_spark_transform_substitutes_inputs_and_writes_single_parquet_file(spark_session: Any, tmp_path: Path) -> None:
    # The Spark write is a directory of part files; the adapter must promote exactly one
    # parquet file to the single-file target the dataset transaction protocol expects.
    adapter = _adapter(spark_session)
    source = tmp_path / "input.parquet"
    adapter.csv_to_parquet(_orders_csv(tmp_path), source)
    target = tmp_path / "out.parquet"

    adapter.execute_transform(
        SqlTransformPlan(
            sql_template="select id, amount * 2 as doubled from {{ input('raw.orders') }} order by id",
            input_paths_by_ref={"raw.orders": source},
            target_path=target,
        )
    )

    assert target.is_file()
    assert not (tmp_path / "out").exists()  # no leftover Spark output directory
    assert [(row["id"], row["doubled"]) for row in adapter.preview_parquet(target, limit=10)] == [
        ("O-1", 200),
        ("O-2", 400),
        ("O-3", 600),
    ]


def test_spark_and_duckdb_produce_equivalent_transform_output(spark_session: Any, tmp_path: Path) -> None:
    # C14: swapping DuckDB for Spark must not change SQL transform semantics.
    duck = DuckDBComputeAdapter()
    spark = _adapter(spark_session)
    source = tmp_path / "input.parquet"
    duck.csv_to_parquet(_orders_csv(tmp_path), source)
    sql = "select id, amount + 1 as bumped from {{ input('raw.orders') }} order by id"

    duck_out = tmp_path / "duck.parquet"
    spark_out = tmp_path / "spark.parquet"
    duck.execute_transform(SqlTransformPlan(sql, {"raw.orders": source}, duck_out))
    spark.execute_transform(SqlTransformPlan(sql, {"raw.orders": source}, spark_out))

    duck_rows = [(r["id"], r["bumped"]) for r in duck.preview_parquet(duck_out, limit=10)]
    spark_rows = [(r["id"], r["bumped"]) for r in spark.preview_parquet(spark_out, limit=10)]
    assert spark_rows == duck_rows == [("O-1", 101), ("O-2", 201), ("O-3", 301)]


def test_spark_unsupported_plan_kind_is_validation_error(spark_session: Any) -> None:
    adapter = _adapter(spark_session)
    with pytest.raises(ValidationFailed, match="only supports SqlTransformPlan"):
        adapter.execute_transform(object())  # type: ignore[arg-type]


def test_spark_invalid_csv_input_is_validation_error(spark_session: Any, tmp_path: Path) -> None:
    # A Spark analysis/parse failure (e.g. missing input) is a non-retryable validation
    # error, not a transient cluster failure.
    adapter = _adapter(spark_session)
    with pytest.raises(ValidationFailed, match="invalid csv input"):
        adapter.csv_to_parquet(tmp_path / "does-not-exist.csv", tmp_path / "out.parquet")


def test_spark_engine_transform_commits_with_lineage_and_health(spark_session: Any, tmp_path: Path) -> None:
    # engine e2e: the composition root runs the clean_orders transform on Spark without any
    # change to the transform service core; the output commits a version with lineage.
    foundry = _spark_foundry(tmp_path, spark_session)
    ctx = demo_admin_context()
    _seed_clean_orders(foundry, ctx)

    result = foundry.transforms.run("clean_orders", ctx=ctx)
    assert result.row_count == 3
    versions = foundry.datasets.list_versions("clean.orders", ctx=ctx)
    assert any(v["id"] == result.version_id for v in versions)
    preview = foundry.datasets.preview("clean.orders", ctx=ctx, version=result.version_id)
    assert len(preview) == 3


def test_spark_transform_failure_aborts_output_transaction(spark_session: Any, tmp_path: Path) -> None:
    # failure-injection + partial-success + recovery-cleanup + operator-evidence: a Spark job
    # failure must leave no committed output version and a FAILED run.
    foundry = _spark_foundry(tmp_path, spark_session, compute=_FailingSparkTransform(session=spark_session))
    ctx = demo_admin_context()
    _seed_clean_orders(foundry, ctx)

    with pytest.raises((AdapterError, ValidationFailed)):
        foundry.transforms.run("clean_orders", ctx=ctx)

    assert foundry.datasets.list_versions("clean.orders", ctx=ctx) == []
    failed = [r for r in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if r["status"] == "FAILED"]
    assert failed


class _FailingSparkTransform(SparkComputeAdapter):
    """Spark compute whose transform execution always fails (job timeout)."""

    def execute_transform(self, plan: Any) -> None:
        raise self._compute_error("execute_transform", TimeoutError("spark job timed out"))


def _spark_foundry(tmp_path: Path, spark_session: Any, *, compute: SparkComputeAdapter | None = None) -> FoundryLite:
    dependencies = create_local_core_dependencies(
        storage_root=tmp_path / f"runtime-{uuid4().hex}",
        db_url=f"sqlite:///{tmp_path / f'meta-{uuid4().hex}.db'}",
    )
    return FoundryLite(dependencies=replace(dependencies, compute_adapter=compute or _adapter(spark_session)))


def _seed_clean_orders(foundry: FoundryLite, ctx: Any) -> None:
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", "examples/supply-chain-demo/data/orders.csv", ctx=ctx)
