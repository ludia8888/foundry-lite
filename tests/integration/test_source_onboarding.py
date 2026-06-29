from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select


def test_source_wizard_explores_customer_erp_and_runs_managed_append_sync(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()

    credential = foundry.sources.create_credential(
        credential_name="erp_db",
        display_name="Customer ERP DB",
        kind="postgres_jdbc",
        auth_scheme="database_url",
        secret_value=f"sqlite:///{source_db}",
        idempotency_key="source-credential-erp-db",
        ctx=ctx,
    )
    secret_ref = cast(dict[str, object], credential["secretRef"])
    assert secret_ref["value"] == "***REDACTED***"
    assert "sqlite:///" not in str(credential)
    assert foundry.sources.get_credential("erp_db", ctx=ctx)["credentialName"] == "erp_db"

    agent = foundry.sources.register_agent(
        agent_id="customer_vpn_agent",
        display_name="Customer VPN Agent",
        mode="agent_proxy",
        capabilities={"jdbc": True, "rest": True},
        network_summary={"region": "customer-dmz"},
        idempotency_key="source-agent-customer-vpn",
        ctx=ctx,
    )
    assert agent["status"] == "registered"
    assert foundry.sources.heartbeat_agent("customer_vpn_agent", ctx=ctx)["status"] == "online"
    network_policy = foundry.sources.create_network_policy(
        policy_name="customer_erp_vpn",
        display_name="Customer ERP VPN",
        mode="agent_proxy",
        agent_id="customer_vpn_agent",
        allowed_hosts=["erp.customer.local"],
        idempotency_key="source-network-customer-erp",
        ctx=ctx,
    )
    assert network_policy["agentId"] == "customer_vpn_agent"

    exploration = foundry.sources.explore_source(
        source_name="customer_erp",
        source_type="postgres_jdbc",
        request={
            "databaseUrlSecretRef": secret_ref["name"],
            "tableName": "orders",
            "checkpointColumn": "id",
            "sampleLimit": 2,
        },
        ctx=ctx,
    )
    result_summary = cast(dict[str, object], exploration["resultSummary"])
    assert exploration["status"] == "succeeded"
    assert len(cast(list[object], result_summary["sample"])) == 2
    assert _dataset_version_count_or_zero(foundry, "raw.orders", ctx.tenant_id) == 0

    sync = foundry.sources.create_managed_sync(
        sync_name="orders_incremental",
        source_name="customer_erp",
        display_name="Orders incremental",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.orders",
        schedule={"mode": "manual"},
        config_summary={
            "databaseUrlSecretRef": secret_ref["name"],
            "tableName": "orders",
            "checkpointColumn": "id",
            "batchLimit": 2,
        },
        idempotency_key="source-sync-orders",
        ctx=ctx,
    )
    assert sync["mode"] == "APPEND"

    first_run = foundry.sources.start_managed_sync_run(
        "orders_incremental",
        idempotency_key="source-sync-run-orders-1",
        batch_limit=2,
        ctx=ctx,
    )
    replay = foundry.sources.start_managed_sync_run(
        "orders_incremental",
        idempotency_key="source-sync-run-orders-1",
        batch_limit=2,
        ctx=ctx,
    )

    assert replay["runId"] == first_run["runId"]
    assert first_run["status"] == "succeeded"
    assert first_run["checkpointEnd"] == {"checkpointColumn": "id", "lastValue": 2}
    assert len(foundry.datasets.preview("raw.orders", ctx=ctx)) == 2
    assert foundry.sources.list_managed_sync_runs("orders_incremental", ctx=ctx)[0]["runId"] == first_run["runId"]
    assert foundry.sources.get_managed_sync_run(cast(str, first_run["runId"]), ctx=ctx)["status"] == "succeeded"


def test_source_csv_upload_commits_dataset_and_registers_source(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")

    with csv_path.open("rb") as source:
        result = foundry.sources.upload_csv(
            source_name="orders_csv",
            display_name="Orders CSV",
            dataset_ref="raw.orders_csv",
            file_name="orders.csv",
            source=source,
            idempotency_key="source-csv-upload",
            sync_name="orders-csv-first-sync",
            primary_key=["order_id"],
            ctx=ctx,
        )

    source_view = cast(dict[str, object], result["source"])
    commit_result = cast(dict[str, object], result["commitResult"])
    operations_path = cast(str, result["operationsPath"])

    assert source_view["kind"] == "csv_upload"
    assert source_view["targetDatasetRef"] == "raw.orders_csv"
    assert commit_result["rowCount"] == 1
    assert operations_path.startswith("/api/operations/runs/sync/")
    assert foundry.datasets.preview("raw.orders_csv", ctx=ctx)[0]["order_id"] == "O-1"
    saved_source = foundry.sources.get_source("orders_csv", ctx=ctx)
    assert cast(str, saved_source["configFingerprint"]).startswith("sha256:")


def test_source_debezium_start_fails_closed_on_fingerprint_mismatch(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    created = foundry.sources.create_debezium_source(
        source_name="orders_cdc",
        display_name="Orders CDC",
        dataset_ref="raw.orders_cdc",
        stream_name="debezium.orders",
        topic="db.public.orders",
        secret_refs={"databasePasswordSecretRef": "orders-db-password"},
        idempotency_key="source-cdc-create",
        ctx=ctx,
    )

    source_view = cast(dict[str, object], created["source"])
    assert source_view["kind"] == "debezium_cdc"
    with pytest.raises(ConflictDetected, match="source config changed"):
        foundry.sources.start_debezium_sync(
            "orders_cdc",
            expected_config_fingerprint="sha256:stale",
            idempotency_key="source-cdc-start",
            ctx=ctx,
        )


def test_source_rejects_raw_secret_values(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()

    with pytest.raises(ValidationFailed, match="secretRef only") as exc_info:
        foundry.sources.create_debezium_source(
            source_name="bad_cdc",
            display_name="Bad CDC",
            dataset_ref="raw.bad_cdc",
            stream_name="debezium.bad",
            topic="db.public.bad",
            secret_refs={"password": "raw-password"},
            idempotency_key="source-cdc-raw-secret",
            ctx=ctx,
        )

    assert exc_info.value.details == {"password": "***REDACTED***"}


def test_source_media_upload_wraps_media_transaction_commit(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="demo",
        name="invoices",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="internal",
    )
    media_path = tmp_path / "invoice.pdf"
    media_path.write_bytes(b"%PDF-1.4\n")

    with media_path.open("rb") as source:
        result = foundry.sources.upload_media(
            source_name="invoice_media",
            display_name="Invoice Media",
            media_set_id=media_set.media_set_id,
            logical_path="invoices/1.pdf",
            file_name="invoice.pdf",
            source=source,
            supplied_mime_type="application/pdf",
            schema_type="document",
            format="pdf",
            security_envelope={"classification": "internal"},
            idempotency_key="source-media-upload",
            ctx=ctx,
        )

    source_view = cast(dict[str, object], result["source"])
    media_commit = cast(dict[str, object], result["mediaCommitResult"])

    assert source_view["kind"] == "media_upload"
    assert media_commit["mediaItemVersionId"]
    assert media_commit["committedVersionIds"]


def _foundry(tmp_path: Path) -> FoundryLite:
    return FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))


def _sqlite_customer_erp(tmp_path: Path) -> Path:
    source_db = tmp_path / "customer_erp.db"
    conn = sqlite3.connect(source_db)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_no TEXT, amount REAL)")
    conn.executemany(
        "INSERT INTO orders (id, order_no, amount) VALUES (?, ?, ?)",
        [(1, "O-1001", 10.5), (2, "O-1002", 22.0), (3, "O-1003", 33.25)],
    )
    conn.commit()
    conn.close()
    return source_db


def _dataset_version_count_or_zero(foundry: FoundryLite, dataset_ref: str, tenant_id: str) -> int:
    namespace, name = dataset_ref.split(".", 1)
    with foundry.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        dataset_id = sql_conn.execute(
            select(db.datasets.c.id).where(
                db.datasets.c.tenant_id == tenant_id,
                db.datasets.c.namespace == namespace,
                db.datasets.c.name == name,
            )
        ).scalar_one_or_none()
        if dataset_id is None:
            return 0
        return int(
            sql_conn.execute(
                select(func.count())
                .select_from(db.dataset_versions)
                .where(db.dataset_versions.c.dataset_id == dataset_id)
            ).scalar_one()
        )
