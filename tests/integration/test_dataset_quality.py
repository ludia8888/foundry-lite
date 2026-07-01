from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.dataset.transactions import DatasetTransactionService
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure import schema as db
from sqlalchemy import select


def test_commit_dataset_version_aborts_when_primary_key_check_fails(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "duplicate_orders.csv"
    csv_path.write_text(
        "order_id,customer_id,source_status\nO-1,C-1,PENDING\nO-1,C-2,REVIEW\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed) as exc_info:
        foundry.datasets.upload_csv("raw.erp_orders", csv_path, ctx=ctx)

    runs = foundry.operations.list_runs(ctx=ctx)
    assert runs["syncRuns"][0]["status"] == "FAILED"
    assert foundry.datasets.list_versions("raw.erp_orders", ctx=ctx) == []
    failures = exc_info.value.details["failures"]
    assert any(failure["contract_status"] == "BLOCK_COMMIT" for failure in failures)


def test_composite_primary_key_uses_tuple_uniqueness(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.order_lines", ctx=ctx, primary_key=["order_id", "line_id"])
    valid_csv = tmp_path / "valid_order_lines.csv"
    valid_csv.write_text(
        "order_id,line_id,amount\nO-1,1,100\nO-1,2,200\n",
        encoding="utf-8",
    )
    invalid_csv = tmp_path / "duplicate_order_lines.csv"
    invalid_csv.write_text(
        "order_id,line_id,amount\nO-1,1,100\nO-1,1,150\n",
        encoding="utf-8",
    )

    committed = foundry.datasets.upload_csv("raw.order_lines", valid_csv, ctx=ctx)
    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        foundry.datasets.upload_csv("raw.order_lines", invalid_csv, ctx=ctx)

    failures = exc_info.value.details["failures"]
    assert committed.version_number == 1
    assert [str(row["line_id"]) for row in foundry.datasets.preview("raw.order_lines", ctx=ctx)] == ["1", "2"]
    assert any(failure["check"] == "unique_tuple" and failure["status"] == "failed" for failure in failures)


def test_dataset_health_check_reads_candidate_not_latest(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.health_orders", ctx=ctx, primary_key=["order_id"])
    latest_csv = tmp_path / "latest_orders.csv"
    latest_csv.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    committed = foundry.datasets.upload_csv("raw.health_orders", latest_csv, ctx=ctx)

    invalid_candidate = tmp_path / "candidate_orders.csv"
    invalid_candidate.write_text("order_id,amount\nO-2,200\nO-2,201\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        foundry.datasets.upload_csv("raw.health_orders", invalid_candidate, ctx=ctx)

    versions = foundry.datasets.list_versions("raw.health_orders", ctx=ctx)
    preview = foundry.datasets.preview("raw.health_orders", ctx=ctx)
    failures = exc_info.value.details["failures"]
    assert [version["id"] for version in versions] == [committed.version_id]
    assert [(row["order_id"], row["amount"]) for row in preview] == [("O-1", 100)]
    assert any(failure["check"] == "unique" and failure["status"] == "failed" for failure in failures)


def test_schema_validation_records_reference_version(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.contract_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "contract_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")

    committed = foundry.datasets.upload_csv("raw.contract_orders", csv_path, ctx=ctx)

    with foundry.engine.begin() as conn:
        schema_row = (
            conn.execute(select(db.dataset_schemas).where(db.dataset_schemas.c.dataset_id == committed.dataset_id))
            .mappings()
            .one()
        )
        version_row = (
            conn.execute(select(db.dataset_versions).where(db.dataset_versions.c.id == committed.version_id))
            .mappings()
            .one()
        )
        check_results = (
            conn.execute(
                select(db.dataset_check_results).where(
                    db.dataset_check_results.c.transaction_id == committed.transaction_id
                )
            )
            .mappings()
            .all()
        )

    assert check_results
    assert version_row["schema_version"] == schema_row["version"]
    assert {row["validated_against_schema_version_id"] for row in check_results} == {schema_row["id"]}
    assert {row["validated_against_schema_version"] for row in check_results} == {schema_row["version"]}


def test_contract_version_is_pinned_to_run(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.pinned_contract_orders", ctx=ctx, primary_key=["order_id"])
    first_csv = tmp_path / "pinned_contract_orders_v1.csv"
    first_csv.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    second_csv = tmp_path / "pinned_contract_orders_v2.csv"
    second_csv.write_text("order_id,amount,region\nO-2,200,APAC\n", encoding="utf-8")

    first = foundry.datasets.upload_csv("raw.pinned_contract_orders", first_csv, ctx=ctx)
    second = foundry.datasets.upload_csv("raw.pinned_contract_orders", second_csv, ctx=ctx)

    with foundry.engine.begin() as conn:
        schema_rows = (
            conn.execute(
                select(db.dataset_schemas)
                .where(db.dataset_schemas.c.dataset_id == first.dataset_id)
                .order_by(db.dataset_schemas.c.version)
            )
            .mappings()
            .all()
        )
        first_results = _check_results_for_transaction(conn, first.transaction_id)
        second_results = _check_results_for_transaction(conn, second.transaction_id)

    assert [row["version"] for row in schema_rows] == [1, 2]
    assert {row["validated_against_schema_version"] for row in first_results} == {1}
    assert {row["validated_against_schema_version_id"] for row in first_results} == {schema_rows[0]["id"]}
    assert {row["validated_against_schema_version"] for row in second_results} == {2}
    assert {row["validated_against_schema_version_id"] for row in second_results} == {schema_rows[1]["id"]}


def test_quality_result_history_lists_dataset_commit_results(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    dataset_ref = "raw.quality_history_orders"
    foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=["order_id"])
    first_csv = tmp_path / "quality_history_orders_v1.csv"
    first_csv.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    second_csv = tmp_path / "quality_history_orders_v2.csv"
    second_csv.write_text("order_id,amount\nO-2,200\n", encoding="utf-8")

    first = foundry.datasets.upload_csv(dataset_ref, first_csv, ctx=ctx)
    second = foundry.datasets.upload_csv(dataset_ref, second_csv, ctx=ctx)

    history = foundry.datasets.list_quality_results(dataset_ref, limit=20, ctx=ctx)
    limited = foundry.datasets.list_quality_results(dataset_ref, limit=1, ctx=ctx)
    summary = foundry.datasets.get_quality_result_summary(dataset_ref, latest_limit=2, ctx=ctx)
    transaction_ids = {row["transactionId"] for row in history["results"]}

    assert history["datasetRef"] == dataset_ref
    assert first.transaction_id in transaction_ids
    assert second.transaction_id in transaction_ids
    assert len(limited["results"]) == 1
    assert all(row["checkedManifestHash"] for row in history["results"])
    assert all(row["validatedAgainstSchemaVersionId"] for row in history["results"])
    assert {row["checkType"] for row in history["results"]} >= {"row_count_min", "not_null", "unique"}
    assert summary["datasetRef"] == dataset_ref
    assert summary["totalResults"] == len(history["results"])
    assert summary["statusCounts"] == [{"status": "PASS", "count": summary["totalResults"]}]
    assert len(summary["latestResults"]) == 2
    assert {row["checkType"] for row in summary["checkTypeStatusCounts"]} >= {"row_count_min", "not_null", "unique"}


def test_persisted_quality_contract_check_blocks_later_commit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.persisted_contract_orders", ctx=ctx, primary_key=["order_id"])

    created = foundry.datasets.create_quality_contract_check(
        "raw.persisted_contract_orders",
        config={"type": "row_count_min", "min": 3},
        ctx=ctx,
    )
    replay = foundry.datasets.create_quality_contract_check(
        "raw.persisted_contract_orders",
        config={"type": "row_count_min", "min": 3},
        ctx=ctx,
    )
    too_small = tmp_path / "persisted_contract_orders.csv"
    too_small.write_text("order_id,amount\nO-1,100\nO-2,200\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        foundry.datasets.upload_csv("raw.persisted_contract_orders", too_small, ctx=ctx)

    listed = foundry.datasets.list_quality_contract_checks("raw.persisted_contract_orders", ctx=ctx)
    failures = exc_info.value.details["failures"]
    assert created["isIdempotentReplay"] is False
    assert replay["isIdempotentReplay"] is True
    assert replay["check"]["id"] == created["check"]["id"]
    assert created["check"]["id"] in [check["id"] for check in listed["checks"]]
    assert any(
        failure["check"] == "row_count_min" and failure["contract_status"] == "BLOCK_COMMIT" for failure in failures
    )


def test_updated_quality_contract_check_controls_later_commit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    dataset_ref = "raw.updated_contract_orders"
    foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=["order_id"])
    created = foundry.datasets.create_quality_contract_check(
        dataset_ref,
        config={"type": "row_count_min", "min": 3},
        ctx=ctx,
    )
    two_rows = tmp_path / "updated_contract_orders_two.csv"
    two_rows.write_text("order_id,amount\nO-1,100\nO-2,200\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="dataset checks failed"):
        foundry.datasets.upload_csv(dataset_ref, two_rows, ctx=ctx)

    disabled = foundry.datasets.update_quality_contract_check(
        dataset_ref,
        created["check"]["id"],
        enabled=False,
        ctx=ctx,
    )
    passed = foundry.datasets.upload_csv(dataset_ref, two_rows, ctx=ctx)
    strengthened = foundry.datasets.update_quality_contract_check(
        dataset_ref,
        created["check"]["id"],
        config={"type": "row_count_min", "min": 4},
        enabled=True,
        ctx=ctx,
    )
    three_rows = tmp_path / "updated_contract_orders_three.csv"
    three_rows.write_text("order_id,amount\nO-1,100\nO-2,200\nO-3,300\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        foundry.datasets.upload_csv(dataset_ref, three_rows, ctx=ctx)

    assert disabled["enabled"] is False
    assert passed.row_count == 2
    assert strengthened["config"] == {"type": "row_count_min", "min": 4, "severity": "error"}
    assert any(failure["check"] == "row_count_min" for failure in exc_info.value.details["failures"])


def test_active_data_contract_version_pins_check_snapshot(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    dataset_ref = "raw.versioned_contract_orders"
    foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=["order_id"])
    created = foundry.datasets.create_quality_contract_check(
        dataset_ref,
        config={"type": "row_count_min", "min": 2},
        ctx=ctx,
    )
    version_result = foundry.datasets.create_data_contract_version(
        dataset_ref,
        owner_user_id="data-owner",
        description="minimum two order rows",
        ctx=ctx,
    )
    contract_version = version_result["contractVersion"]
    activated = foundry.datasets.activate_data_contract_version(dataset_ref, contract_version["id"], ctx=ctx)
    foundry.datasets.update_quality_contract_check(
        dataset_ref,
        created["check"]["id"],
        config={"type": "row_count_min", "min": 4},
        ctx=ctx,
    )
    two_rows = tmp_path / "versioned_contract_orders.csv"
    two_rows.write_text("order_id,amount\nO-1,100\nO-2,200\n", encoding="utf-8")

    committed = foundry.datasets.upload_csv(dataset_ref, two_rows, ctx=ctx)
    versions = foundry.datasets.list_data_contract_versions(dataset_ref, ctx=ctx)
    fetched = foundry.datasets.get_data_contract_version(dataset_ref, contract_version["id"], ctx=ctx)
    history = foundry.datasets.list_quality_results(dataset_ref, limit=20, ctx=ctx)
    sync_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"]
        if run["committed_version_id"] == committed.version_id
    )
    detail = foundry.operations.run_detail("sync", str(sync_run["id"]), ctx=ctx)

    with foundry.engine.begin() as conn:
        check_results = _check_results_for_transaction(conn, committed.transaction_id)

    assert version_result["isIdempotentReplay"] is False
    assert activated["status"] == "ACTIVE"
    assert fetched["checks"][0]["config"] == {"type": "row_count_min", "min": 2}
    assert versions["contractVersions"][0]["id"] == contract_version["id"]
    assert committed.row_count == 2
    assert {row["data_contract_version_id"] for row in check_results} == {contract_version["id"]}
    assert {row["dataContractVersionId"] for row in history["results"]} == {contract_version["id"]}
    assert detail["quality"]["dataContractVersionIds"] == [contract_version["id"]]


def test_active_data_contract_version_pins_accepted_values_policy(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    dataset_ref = "raw.contract_policy_orders"
    foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=["order_id"])
    with pytest.raises(ValidationFailed, match="accepted values check requires values"):
        foundry.datasets.create_quality_contract_check(
            dataset_ref,
            config={"type": "accepted_values", "column": "status", "values": []},
            ctx=ctx,
        )
    created = foundry.datasets.create_quality_contract_check(
        dataset_ref,
        config={"type": "accepted_values", "column": "status", "values": ["PENDING", "APPROVED"]},
        ctx=ctx,
    )
    first_version = foundry.datasets.create_data_contract_version(
        dataset_ref,
        owner_user_id="data-owner",
        description="initial order status policy",
        ctx=ctx,
    )["contractVersion"]
    foundry.datasets.activate_data_contract_version(dataset_ref, first_version["id"], ctx=ctx)
    foundry.datasets.update_quality_contract_check(
        dataset_ref,
        created["check"]["id"],
        config={"type": "accepted_values", "column": "status", "values": ["PENDING", "APPROVED", "CANCELLED"]},
        ctx=ctx,
    )
    cancelled_order = tmp_path / "contract_policy_cancelled.csv"
    cancelled_order.write_text("order_id,status\nO-1,CANCELLED\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        foundry.datasets.upload_csv(dataset_ref, cancelled_order, ctx=ctx)

    second_version = foundry.datasets.create_data_contract_version(
        dataset_ref,
        owner_user_id="data-owner",
        description="allow cancelled orders",
        ctx=ctx,
    )["contractVersion"]
    foundry.datasets.activate_data_contract_version(dataset_ref, second_version["id"], ctx=ctx)
    committed = foundry.datasets.upload_csv(dataset_ref, cancelled_order, ctx=ctx)
    with foundry.engine.begin() as conn:
        check_results = _check_results_for_transaction(conn, committed.transaction_id)
    accepted_result = next(row for row in check_results if row["details"]["check"] == "accepted_values")

    failure = next(item for item in exc_info.value.details["failures"] if item["check"] == "accepted_values")
    assert failure["contract_status"] == "BLOCK_COMMIT"
    assert failure["invalid_count"] == 1
    assert failure["sample_invalid_values"] == ["CANCELLED"]
    preview = foundry.datasets.preview(dataset_ref, ctx=ctx)
    assert [(row["order_id"], row["status"]) for row in preview] == [("O-1", "CANCELLED")]
    assert accepted_result["data_contract_version_id"] == second_version["id"]
    assert accepted_result["status"] == "PASS"


def test_schema_evolution_widening_is_visible_in_transaction_metadata(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.schema_evolution_orders", ctx=ctx, primary_key=["order_id"])
    first_csv = tmp_path / "schema_evolution_orders_v1.csv"
    first_csv.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    second_csv = tmp_path / "schema_evolution_orders_v2.csv"
    second_csv.write_text("order_id,amount\nO-2,100.5\n", encoding="utf-8")

    foundry.datasets.upload_csv("raw.schema_evolution_orders", first_csv, ctx=ctx)
    second = foundry.datasets.upload_csv("raw.schema_evolution_orders", second_csv, ctx=ctx)

    with foundry.engine.begin() as conn:
        tx_row = (
            conn.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == second.transaction_id))
            .mappings()
            .one()
        )

    evolution = tx_row["metadata"]["schemaEvolution"]
    assert evolution["status"] == "compatible_with_warning"
    assert evolution["consumerCompatibility"] == "review_recommended"
    assert evolution["sourceSchemaVersion"] == 1
    assert evolution["targetSchemaVersion"] == 2
    assert evolution["changes"] == [
        {
            "kind": "type_widening",
            "column": "amount",
            "status": "warning",
            "reason": "numeric widening is compatible but downstream schema hashes should refresh",
            "previousType": "integer",
            "nextType": "float",
        }
    ]


def test_operations_run_detail_exposes_candidate_quality_report(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.quality_report_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "quality_report_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")

    committed = foundry.datasets.upload_csv("raw.quality_report_orders", csv_path, ctx=ctx)

    sync_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"]
        if run["committed_version_id"] == committed.version_id
    )
    detail = foundry.operations.run_detail("sync", str(sync_run["id"]), ctx=ctx)
    quality = detail["quality"]
    assert quality is not None
    with foundry.engine.begin() as conn:
        schema_row = (
            conn.execute(select(db.dataset_schemas).where(db.dataset_schemas.c.dataset_id == committed.dataset_id))
            .mappings()
            .one()
        )
        check_results = _check_results_for_transaction(conn, committed.transaction_id)

    assert quality["transactionId"] == committed.transaction_id
    assert quality["summary"]["total"] == len(check_results)
    assert quality["summary"]["pass"] == len(check_results)
    assert quality["summary"]["warn"] == 0
    assert quality["summary"]["quarantine"] == 0
    assert quality["summary"]["blockCommit"] == 0
    assert quality["schemaReferences"] == [{"id": schema_row["id"], "version": schema_row["version"]}]
    assert quality["checkedManifestHashes"] == sorted({row["checked_manifest_hash"] for row in check_results})
    assert {
        (row["status"], row["validatedAgainstSchemaVersion"], row["validatedAgainstSchemaVersionId"])
        for row in quality["results"]
    } == {("PASS", schema_row["version"], schema_row["id"])}


def test_quality_check_pins_candidate_manifest_hash(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.hash_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "hash_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")

    committed = foundry.datasets.upload_csv("raw.hash_orders", csv_path, ctx=ctx)

    with foundry.engine.begin() as conn:
        schema_row = (
            conn.execute(select(db.dataset_schemas).where(db.dataset_schemas.c.dataset_id == committed.dataset_id))
            .mappings()
            .one()
        )
        file_row = (
            conn.execute(select(db.dataset_files).where(db.dataset_files.c.dataset_version_id == committed.version_id))
            .mappings()
            .one()
        )
        check_results = (
            conn.execute(
                select(db.dataset_check_results).where(
                    db.dataset_check_results.c.transaction_id == committed.transaction_id
                )
            )
            .mappings()
            .all()
        )

    expected_hash = _candidate_manifest_fingerprint(
        row_count=committed.row_count,
        byte_size=file_row["byte_size"],
        content_hash=file_row["content_hash"],
        schema_hash=schema_row["schema_hash"],
    )
    assert check_results
    assert {row["status"] for row in check_results} == {"PASS"}
    assert {row["checked_manifest_hash"] for row in check_results} == {expected_hash}
    assert {row["details"]["contract_status"] for row in check_results} == {"PASS"}
    assert {row["details"]["status"] for row in check_results} == {"passed"}


def test_candidate_tamper_between_check_and_commit_is_rejected(
    foundry: FoundryLite,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.tamper_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "tamper_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    original_store = DatasetTransactionService._store_finalized_dataset_commit

    def tamper_then_store(
        self: DatasetTransactionService,
        *args: Any,
        **kwargs: Any,
    ) -> object:
        request = args[2]
        request.staged_parquet.write_bytes(b"tampered after quality checks")
        return original_store(self, *args, **kwargs)

    monkeypatch.setattr(DatasetTransactionService, "_store_finalized_dataset_commit", tamper_then_store)

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        foundry.datasets.upload_csv("raw.tamper_orders", csv_path, ctx=ctx)

    failures = exc_info.value.details["failures"]
    assert any(
        failure["check"] == "candidate_manifest_hash" and failure["contract_status"] == "BLOCK_COMMIT"
        for failure in failures
    )
    assert foundry.datasets.list_versions("raw.tamper_orders", ctx=ctx) == []


def test_warn_does_not_block_commit_but_is_visible(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.warn_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.warn_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "warn_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    foundry.datasets.upload_csv("raw.warn_orders", csv_path, ctx=ctx)
    sql_path = tmp_path / "warn_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.warn_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "warn_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.warn_orders"},
        output_dataset_ref="clean.warn_orders",
        checks=[{"type": "row_count_min", "min": 2, "severity": "warning"}],
        ctx=ctx,
    )

    committed = foundry.transforms.run("warn_orders", ctx=ctx)

    with foundry.engine.begin() as conn:
        check_results = (
            conn.execute(
                select(db.dataset_check_results).where(
                    db.dataset_check_results.c.transaction_id == committed.transaction_id
                )
            )
            .mappings()
            .all()
        )

    assert [version["id"] for version in foundry.datasets.list_versions("clean.warn_orders", ctx=ctx)] == [
        committed.version_id
    ]
    warning_rows = [row for row in check_results if row["status"] == "WARN"]
    assert warning_rows
    assert warning_rows[0]["details"]["status"] == "failed"
    assert warning_rows[0]["details"]["contract_status"] == "WARN"


def test_unknown_quality_check_type_blocks_commit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.unknown_check_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.unknown_check_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "unknown_check_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    foundry.datasets.upload_csv("raw.unknown_check_orders", csv_path, ctx=ctx)
    sql_path = tmp_path / "unknown_check_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.unknown_check_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "unknown_check_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.unknown_check_orders"},
        output_dataset_ref="clean.unknown_check_orders",
        checks=[{"type": "custom"}],
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="unsupported dataset quality check type"):
        foundry.transforms.run("unknown_check_orders", ctx=ctx)

    assert foundry.datasets.list_versions("clean.unknown_check_orders", ctx=ctx) == []


def test_quarantine_routes_bad_records_to_record_dlq(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.quarantine_orders", ctx=ctx, primary_key=["line_id"])
    foundry.datasets.ensure("clean.quarantine_orders", ctx=ctx, primary_key=["line_id"])
    csv_path = tmp_path / "quarantine_orders.csv"
    csv_path.write_text("line_id,order_id,amount\nL-1,O-1,100\nL-2,,200\n", encoding="utf-8")
    foundry.datasets.upload_csv("raw.quarantine_orders", csv_path, ctx=ctx)
    sql_path = tmp_path / "quarantine_orders.sql"
    sql_path.write_text(
        "select line_id, nullif(order_id, '') as order_id, amount from {{ input('raw.quarantine_orders') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "quarantine_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.quarantine_orders"},
        output_dataset_ref="clean.quarantine_orders",
        checks=[{"type": "not_null", "columns": ["order_id"], "severity": "quarantine"}],
        ctx=ctx,
    )

    committed = foundry.transforms.run("quarantine_orders", ctx=ctx)

    preview = foundry.datasets.preview("clean.quarantine_orders", ctx=ctx, version=committed.version_id)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == committed.version_id
    )
    with foundry.engine.begin() as conn:
        check_results = (
            conn.execute(
                select(db.dataset_check_results).where(
                    db.dataset_check_results.c.transaction_id == committed.transaction_id
                )
            )
            .mappings()
            .all()
        )
        dead_letters = (
            conn.execute(
                select(db.dead_letter_records).where(db.dead_letter_records.c.source_run_id == transform_run["id"])
            )
            .mappings()
            .all()
        )

    quarantine_rows = [row for row in check_results if row["status"] == "QUARANTINE"]
    assert [(row["line_id"], row["order_id"]) for row in preview] == [("L-1", "O-1")]
    assert committed.row_count == 1
    assert quarantine_rows
    assert quarantine_rows[0]["details"]["status"] == "failed"
    assert quarantine_rows[0]["details"]["quarantined_rows"] == 1
    assert quarantine_rows[0]["details"]["contract_status"] == "QUARANTINE"
    assert len(dead_letters) == 1
    assert dead_letters[0]["status"] == "QUARANTINED"
    assert dead_letters[0]["error_kind"] == "DATA_QUALITY_CONTRACT"
    assert dead_letters[0]["payload"]["line_id"] == "L-2"
    assert dead_letters[0]["metadata"]["recordDlqKind"] == "data_quality_contract"
    assert dead_letters[0]["metadata"]["checkedManifestHash"]
    audit_types = {event["event_type"] for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]}
    assert "dead_letter_record.quarantined" in audit_types
    with pytest.raises(ConflictDetected, match="contract-specific remediation"):
        foundry.operations.retry_dead_letter_record(
            dead_letters[0]["id"],
            idempotency_key="retry-quality-contract",
            ctx=ctx,
        )


def test_operations_run_detail_includes_failed_row_sample_for_quarantine(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.quality_sample_orders", ctx=ctx, primary_key=["line_id"])
    foundry.datasets.ensure("clean.quality_sample_orders", ctx=ctx, primary_key=["line_id"])
    csv_path = tmp_path / "quality_sample_orders.csv"
    csv_path.write_text("line_id,order_id,amount\nL-1,O-1,100\nL-2,,200\n", encoding="utf-8")
    foundry.datasets.upload_csv("raw.quality_sample_orders", csv_path, ctx=ctx)
    sql_path = tmp_path / "quality_sample_orders.sql"
    sql_path.write_text(
        "select line_id, nullif(order_id, '') as order_id, amount from {{ input('raw.quality_sample_orders') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "quality_sample_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.quality_sample_orders"},
        output_dataset_ref="clean.quality_sample_orders",
        checks=[{"type": "not_null", "columns": ["order_id"], "severity": "quarantine"}],
        ctx=ctx,
    )

    committed = foundry.transforms.run("quality_sample_orders", ctx=ctx)

    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == committed.version_id
    )
    detail = foundry.operations.run_detail("transform", str(transform_run["id"]), ctx=ctx)
    quality = detail["quality"]
    assert quality is not None
    sample = quality["failedRowSamples"][0]
    assert quality["failedRowSampleCount"] == 1
    assert sample["status"] == "QUARANTINED"
    assert sample["errorKind"] == "DATA_QUALITY_CONTRACT"
    assert sample["rowIndex"] == 1
    assert sample["payload"] == {"line_id": "L-2", "order_id": None, "amount": 200}
    assert sample["checkedManifestHash"] in quality["checkedManifestHashes"]
    assert sample["check"] == {"type": "not_null", "columns": ["order_id"], "severity": "quarantine"}


def _candidate_manifest_fingerprint(
    *,
    row_count: int,
    byte_size: int,
    content_hash: str,
    schema_hash: str,
) -> str:
    payload = json.dumps(
        {
            "byte_size": byte_size,
            "content_hash": content_hash,
            "row_count": row_count,
            "schema_hash": schema_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_results_for_transaction(conn: object, transaction_id: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            select(db.dataset_check_results).where(db.dataset_check_results.c.transaction_id == transaction_id)
        )
        .mappings()
        .all()
    )
