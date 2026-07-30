from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
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


def test_managed_source_lifecycle_blocks_runs_without_erasing_sync_history(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    foundry.sources.create_managed_sync(
        sync_name="orders_source_lifecycle",
        source_name="customer_erp_lifecycle",
        display_name="Customer ERP lifecycle",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.lifecycle_orders",
        schedule={"mode": "interval", "everySeconds": 60, "startAt": "2020-01-01T00:00:00Z"},
        config_summary={
            "databaseUrlSecretRef": secret_ref["name"],
            "tableName": "orders",
            "checkpointColumn": "id",
        },
        idempotency_key="source-sync-lifecycle",
        ctx=ctx,
    )
    first_run = foundry.sources.start_managed_sync_run(
        "orders_source_lifecycle", idempotency_key="source-sync-lifecycle-run-1", ctx=ctx
    )
    active_source = foundry.sources.get_source("customer_erp_lifecycle", ctx=ctx)

    disabled = foundry.sources.update_source_status(
        "customer_erp_lifecycle",
        status="disabled",
        expected_config_fingerprint=cast(str, active_source["configFingerprint"]),
        idempotency_key="source-lifecycle-disable",
        ctx=ctx,
    )
    disable_replay = foundry.sources.update_source_status(
        "customer_erp_lifecycle",
        status="disabled",
        expected_config_fingerprint=cast(str, active_source["configFingerprint"]),
        idempotency_key="source-lifecycle-disable",
        ctx=ctx,
    )

    assert active_source["kind"] == "postgres_jdbc"
    assert "tableName" not in cast(dict[str, object], active_source["configSummary"])
    assert active_source["lastRunId"] == first_run["runId"]
    assert cast(dict[str, object], active_source["lastCommitRef"])["rowCount"] == 3
    assert disabled["status"] == "disabled"
    assert disable_replay == disabled
    assert foundry.sources.get_managed_sync("orders_source_lifecycle", ctx=ctx)["status"] == "active"
    assert (
        _decision_for(foundry.sources.preview_due_managed_syncs(ctx=ctx), "orders_source_lifecycle")["reason"]
        == "sync_inactive"
    )
    with pytest.raises(ValidationFailed, match="source is disabled"):
        foundry.sources.start_managed_sync_run(
            "orders_source_lifecycle", idempotency_key="source-sync-lifecycle-blocked", ctx=ctx
        )

    enabled = foundry.sources.update_source_status(
        "customer_erp_lifecycle",
        status="active",
        expected_config_fingerprint=cast(str, disabled["configFingerprint"]),
        idempotency_key="source-lifecycle-enable",
        ctx=ctx,
    )
    second_run = foundry.sources.start_managed_sync_run(
        "orders_source_lifecycle", idempotency_key="source-sync-lifecycle-run-2", ctx=ctx
    )
    assert enabled["status"] == "active"
    assert second_run["status"] == "succeeded"

    with dependencies.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        audit_types = list(
            sql_conn.execute(
                select(db.audit_events.c.event_type)
                .where(db.audit_events.c.resource_id == "customer_erp_lifecycle")
                .order_by(db.audit_events.c.created_at, db.audit_events.c.id)
            ).scalars()
        )
        outbox_types = list(
            sql_conn.execute(
                select(db.outbox_events.c.event_type)
                .where(db.outbox_events.c.aggregate_id == "customer_erp_lifecycle")
                .order_by(db.outbox_events.c.created_at, db.outbox_events.c.id)
            ).scalars()
        )
    assert [event for event in audit_types if event in {"source.disabled", "source.enabled"}] == [
        "source.disabled",
        "source.enabled",
    ]
    assert [event for event in outbox_types if event in {"source.disabled", "source.enabled"}] == [
        "source.disabled",
        "source.enabled",
    ]


def test_source_connection_test_is_live_idempotent_and_durable_without_dataset_commit(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    foundry.sources.create_managed_sync(
        sync_name="orders_connection_test",
        source_name="customer_erp_connection_test",
        display_name="Customer ERP connection test",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.connection_test_orders",
        schedule={"mode": "manual"},
        config_summary={"databaseUrlSecretRef": secret_ref["name"], "tableName": "orders"},
        idempotency_key="source-sync-connection-test",
        ctx=ctx,
    )
    source = foundry.sources.get_source("customer_erp_connection_test", ctx=ctx)

    result = foundry.sources.test_connection(
        "customer_erp_connection_test",
        expected_config_fingerprint=cast(str, source["configFingerprint"]),
        idempotency_key="source-connection-test-live",
        ctx=ctx,
    )
    replay = foundry.sources.test_connection(
        "customer_erp_connection_test",
        expected_config_fingerprint=cast(str, source["configFingerprint"]),
        idempotency_key="source-connection-test-live",
        ctx=ctx,
    )
    restarted = FoundryLite(dependencies=dependencies)
    history = restarted.sources.list_connection_tests("customer_erp_connection_test", ctx=ctx)

    assert result["status"] == "succeeded"
    assert cast(dict[str, object], result["checks"])["summary"] == {"passed": 5, "total": 5}
    assert cast(dict[str, object], cast(dict[str, object], result["checks"])["probe"])["visibleResourceCount"] == 1
    assert result["requestId"] == ctx.request_id
    assert replay["connectionTestId"] == result["connectionTestId"]
    assert replay["isIdempotentReplay"] is True
    assert [row["connectionTestId"] for row in history] == [result["connectionTestId"]]
    assert _dataset_version_count_or_zero(foundry, "raw.connection_test_orders", ctx.tenant_id) == 0
    with dependencies.engine.begin() as conn:
        event_types = conn.execute(
            select(db.audit_events.c.event_type)
            .where(db.audit_events.c.resource_id == "customer_erp_connection_test")
            .order_by(db.audit_events.c.created_at, db.audit_events.c.id)
        ).scalars()
        assert [event for event in event_types if event.startswith("source.connection_test.")] == [
            "source.connection_test.started",
            "source.connection_test.succeeded",
        ]


def test_source_connection_test_persists_redacted_failure_and_rejects_stale_config(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    credential = foundry.sources.create_credential(
        credential_name="broken_erp_db",
        display_name="Broken ERP DB",
        kind="postgres_jdbc",
        auth_scheme="database_url",
        secret_value="not-a-database-url-with-super-secret-value",
        idempotency_key="source-credential-broken-erp-db",
        ctx=ctx,
    )
    secret_ref = cast(dict[str, object], credential["secretRef"])
    foundry.sources.create_managed_sync(
        sync_name="broken_orders_connection_test",
        source_name="broken_customer_erp",
        display_name="Broken customer ERP",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.broken_connection_test_orders",
        schedule={"mode": "manual"},
        config_summary={"databaseUrlSecretRef": secret_ref["name"], "tableName": "orders"},
        idempotency_key="source-sync-broken-connection-test",
        ctx=ctx,
    )
    source = foundry.sources.get_source("broken_customer_erp", ctx=ctx)

    failed = foundry.sources.test_connection(
        "broken_customer_erp",
        expected_config_fingerprint=cast(str, source["configFingerprint"]),
        idempotency_key="source-connection-test-broken",
        ctx=ctx,
    )

    assert failed["status"] == "failed"
    assert failed["requestId"] == ctx.request_id
    assert "super-secret-value" not in str(failed)
    assert foundry.sources.list_connection_tests("broken_customer_erp", ctx=ctx)[0]["status"] == "failed"
    with pytest.raises(ConflictDetected, match="source config changed"):
        foundry.sources.test_connection(
            "broken_customer_erp",
            expected_config_fingerprint="sha256:stale",
            idempotency_key="source-connection-test-stale",
            ctx=ctx,
        )


def test_source_scheduler_interval_starts_due_managed_sync_once_per_slot(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    foundry.sources.create_managed_sync(
        sync_name="orders_hourly",
        source_name="customer_erp",
        display_name="Orders hourly",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.scheduled_orders",
        schedule={"mode": "interval", "everySeconds": 3600, "startAt": "2020-01-01T00:00:00Z", "batchLimit": 1},
        config_summary={
            "databaseUrlSecretRef": secret_ref["name"],
            "tableName": "orders",
            "checkpointColumn": "id",
        },
        idempotency_key="source-sync-scheduled-orders",
        ctx=ctx,
    )

    preview = foundry.sources.preview_due_managed_syncs(ctx=ctx)
    first_tick = foundry.sources.run_due_managed_syncs(ctx=ctx)
    second_tick = foundry.sources.run_due_managed_syncs(ctx=ctx)

    assert len(cast(list[object], preview["due"])) == 1
    assert len(cast(list[object], first_tick["started"])) == 1
    assert len(cast(list[object], second_tick["started"])) == 0
    assert cast(dict[str, object], second_tick["skipped"][0])["reason"] == "slot_already_started"
    assert foundry.datasets.preview("raw.scheduled_orders", ctx=ctx)[0]["id"] == 1
    assert foundry.sources.list_managed_sync_runs("orders_hourly", ctx=ctx)[0]["triggerType"] == "scheduled"


def test_source_schedule_update_is_validated_audited_and_stale_write_safe(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    created = foundry.sources.create_managed_sync(
        sync_name="orders_editable_schedule",
        source_name="customer_erp",
        display_name="Orders editable schedule",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.editable_schedule_orders",
        schedule={"mode": "manual"},
        config_summary={"databaseUrlSecretRef": secret_ref["name"], "tableName": "orders"},
        idempotency_key="source-sync-editable-schedule",
        ctx=ctx,
    )

    updated = foundry.sources.update_managed_sync_schedule(
        "orders_editable_schedule",
        schedule={"mode": "interval", "intervalSeconds": "3600", "batchLimit": 2},
        expected_config_fingerprint=cast(str, created["configFingerprint"]),
        idempotency_key="source-sync-editable-schedule-update",
        ctx=ctx,
    )
    replay = foundry.sources.update_managed_sync_schedule(
        "orders_editable_schedule",
        schedule={"mode": "interval", "everySeconds": 3600, "batchLimit": 2},
        expected_config_fingerprint=cast(str, created["configFingerprint"]),
        idempotency_key="source-sync-editable-schedule-update",
        ctx=ctx,
    )

    assert updated["schedule"] == {"mode": "interval", "everySeconds": 3600, "batchLimit": 2}
    assert replay == updated
    decisions = cast(list[dict[str, object]], foundry.sources.preview_due_managed_syncs(ctx=ctx)["decisions"])
    decision = next(item for item in decisions if item["syncName"] == "orders_editable_schedule")
    assert decision["enabled"] is True
    assert decision["nextDueAt"] is not None
    with pytest.raises(ConflictDetected):
        foundry.sources.update_managed_sync_schedule(
            "orders_editable_schedule",
            schedule={"mode": "disabled"},
            expected_config_fingerprint=cast(str, created["configFingerprint"]),
            idempotency_key="source-sync-editable-schedule-stale",
            ctx=ctx,
        )
    with pytest.raises(ValidationFailed, match="five fields"):
        foundry.sources.update_managed_sync_schedule(
            "orders_editable_schedule",
            schedule={"mode": "cron", "cron": "bad cron"},
            expected_config_fingerprint=cast(str, updated["configFingerprint"]),
            idempotency_key="source-sync-editable-schedule-invalid",
            ctx=ctx,
        )


def test_source_schedule_pause_resume_preserves_config_resets_trigger_and_audits(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    created = foundry.sources.create_managed_sync(
        sync_name="orders_pause_resume",
        source_name="customer_erp",
        display_name="Orders pause resume",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.pause_resume_orders",
        schedule={"mode": "interval", "everySeconds": 3600, "startAt": "2020-01-01T00:00:00Z"},
        config_summary={"databaseUrlSecretRef": secret_ref["name"], "tableName": "orders"},
        idempotency_key="source-sync-pause-resume",
        ctx=ctx,
    )

    paused = foundry.sources.pause_managed_sync_schedule(
        "orders_pause_resume",
        expected_config_fingerprint=cast(str, created["configFingerprint"]),
        idempotency_key="source-sync-pause",
        ctx=ctx,
    )
    pause_replay = foundry.sources.pause_managed_sync_schedule(
        "orders_pause_resume",
        expected_config_fingerprint=cast(str, created["configFingerprint"]),
        idempotency_key="source-sync-pause",
        ctx=ctx,
    )
    paused_tick = foundry.sources.run_due_managed_syncs(ctx=ctx)
    paused_decision = _decision_for(paused_tick, "orders_pause_resume")
    resumed = foundry.sources.resume_managed_sync_schedule(
        "orders_pause_resume",
        expected_config_fingerprint=cast(str, paused["configFingerprint"]),
        idempotency_key="source-sync-resume",
        ctx=ctx,
    )
    resumed_decision = _decision_for(foundry.sources.preview_due_managed_syncs(ctx=ctx), "orders_pause_resume")

    assert paused["status"] == "paused"
    assert pause_replay == paused
    assert paused["schedule"] == created["schedule"]
    assert paused_tick["started"] == []
    assert paused_decision["reason"] == "schedule_paused"
    assert resumed["status"] == "active"
    assert cast(dict[str, object], resumed["schedule"])["everySeconds"] == 3600
    assert resumed_decision["due"] is False
    assert resumed_decision["reason"] == "not_due"
    assert resumed_decision["nextDueAt"] == cast(dict[str, object], resumed["schedule"])["startAt"]
    with pytest.raises(ConflictDetected):
        foundry.sources.pause_managed_sync_schedule(
            "orders_pause_resume",
            expected_config_fingerprint=cast(str, created["configFingerprint"]),
            idempotency_key="source-sync-stale-pause",
            ctx=ctx,
        )
    with dependencies.engine.begin() as conn:
        audit_types = conn.execute(
            select(db.audit_events.c.event_type)
            .where(db.audit_events.c.resource_id == "orders_pause_resume")
            .order_by(db.audit_events.c.created_at, db.audit_events.c.id)
        ).scalars()
        assert [event for event in audit_types if ".schedule." in event] == [
            "source.sync.schedule.paused",
            "source.sync.schedule.resumed",
        ]


def test_source_schedule_auto_pauses_after_failures_and_recovery_run_resumes(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    foundry.sources.create_managed_sync(
        sync_name="orders_auto_pause",
        source_name="customer_erp",
        display_name="Orders automatic failure protection",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.auto_pause_orders",
        schedule={
            "mode": "interval",
            "everySeconds": 60,
            "startAt": "2026-01-01T00:00:00Z",
            "autoPauseAfterFailures": 3,
        },
        config_summary={"databaseUrlSecretRef": secret_ref["name"], "tableName": "recovery_orders"},
        idempotency_key="source-sync-auto-pause",
        ctx=ctx,
    )

    scheduler = foundry._services.source_scheduler  # noqa: SLF001 - deterministic scheduler clock boundary.
    ticks = [
        scheduler.run_due_managed_syncs(ctx=ctx, now=datetime(2026, 1, 1, 0, minute, tzinfo=UTC)) for minute in range(3)
    ]
    paused = foundry.sources.get_managed_sync("orders_auto_pause", ctx=ctx)
    decision = _decision_for(foundry.sources.preview_due_managed_syncs(ctx=ctx), "orders_auto_pause")

    assert [cast(dict[str, object], tick["started"][0])["run"]["status"] for tick in ticks] == [
        "failed",
        "failed",
        "failed",
    ]
    assert paused["status"] == "paused"
    assert decision["autoPaused"] is True
    assert decision["consecutiveFailureCount"] == 3
    assert cast(dict[str, object], decision["lastFailureError"])["requestId"]
    with pytest.raises(ValidationFailed, match="recovery run is required"):
        foundry.sources.resume_managed_sync_schedule(
            "orders_auto_pause",
            expected_config_fingerprint=cast(str, paused["configFingerprint"]),
            idempotency_key="unsafe-auto-pause-resume",
            ctx=ctx,
        )

    with sqlite3.connect(source_db) as conn:
        conn.execute("create table recovery_orders (id integer primary key, order_id text)")
        conn.execute("insert into recovery_orders (id, order_id) values (1, 'RECOVERED-1')")
    recovery = foundry.sources.start_managed_sync_run(
        "orders_auto_pause",
        trigger_type="recovery",
        idempotency_key="source-sync-auto-pause-recovery",
        ctx=ctx,
    )
    replay = foundry.sources.start_managed_sync_run(
        "orders_auto_pause",
        trigger_type="recovery",
        idempotency_key="source-sync-auto-pause-recovery",
        ctx=ctx,
    )
    resumed = foundry.sources.get_managed_sync("orders_auto_pause", ctx=ctx)
    resumed_decision = _decision_for(foundry.sources.preview_due_managed_syncs(ctx=ctx), "orders_auto_pause")

    assert recovery["status"] == "succeeded"
    assert recovery["triggerType"] == "recovery"
    assert replay["runId"] == recovery["runId"]
    assert resumed["status"] == "active"
    assert resumed_decision["autoPaused"] is False
    assert resumed_decision["consecutiveFailureCount"] == 0
    assert resumed_decision["reason"] == "not_due"
    assert foundry.datasets.preview("raw.auto_pause_orders", ctx=ctx)[0]["order_id"] == "RECOVERED-1"
    with dependencies.engine.begin() as conn:
        audit_types = conn.execute(
            select(db.audit_events.c.event_type)
            .where(db.audit_events.c.resource_id == "orders_auto_pause")
            .order_by(db.audit_events.c.created_at, db.audit_events.c.id)
        ).scalars()
        assert [event for event in audit_types if ".schedule.auto_" in event] == [
            "source.sync.schedule.auto_paused",
            "source.sync.schedule.auto_resumed",
        ]


def test_source_scheduler_cron_schedule_decision_uses_minute_slot(tmp_path: Path) -> None:
    source_db = _sqlite_customer_erp(tmp_path)
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)
    foundry.sources.create_managed_sync(
        sync_name="orders_cron",
        source_name="customer_erp",
        display_name="Orders cron",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.cron_orders",
        schedule={"mode": "cron", "cron": "* * * * *", "startAt": "2020-01-01T00:00:00Z"},
        config_summary={"databaseUrlSecretRef": secret_ref["name"], "tableName": "orders"},
        idempotency_key="source-sync-cron-orders",
        ctx=ctx,
    )

    expected_before = _minute_slot()
    tick = foundry.sources.run_due_managed_syncs(ctx=ctx)
    expected_after = _minute_slot()

    started = cast(list[dict[str, object]], tick["started"])
    decision = cast(dict[str, object], started[0]["decision"])
    assert decision["slotStart"] in {expected_before, expected_after}
    assert cast(dict[str, object], started[0]["run"])["triggerType"] == "scheduled"


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
    run_id = cast(str, commit_result["runId"])
    detail = foundry.operations.run_detail("sync", run_id, ctx=ctx)
    source_evidence = cast(dict[str, object], detail["sourceEvidence"])
    assert source_evidence["syncName"] == "orders-csv-first-sync"
    assert source_evidence["sourceType"] == "file.csv"
    assert source_evidence["transactionId"] == commit_result["transactionId"]
    assert source_evidence["committedVersionId"] == commit_result["versionId"]
    assert source_evidence["operationPath"] == operations_path
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


def test_source_debezium_object_index_tick_tracks_cdc_cursor(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    _seed_order_cdc_source(foundry, tmp_path)
    foundry.sources.create_debezium_source(
        source_name="orders_cdc",
        display_name="Orders CDC",
        dataset_ref="raw_cdc.erp_orders",
        stream_name="debezium.orders",
        topic="db.public.orders",
        primary_key=["order_id"],
        idempotency_key="source-cdc-create",
        ctx=ctx,
    )
    _commit_cdc_version(foundry, tmp_path, "topic:0:1", 1, "APPROVED")
    _commit_cdc_version(foundry, tmp_path, "topic:0:2", 2, "SHIPPED")

    initial_plan = foundry.sources.debezium_operation_plan("orders_cdc", object_type_api_name="Order", ctx=ctx)
    capture_semantics = cast(dict[str, object], initial_plan["captureSemantics"])
    initial_status = cast(dict[str, object], initial_plan["objectIndexingStatus"])
    initial_backlog = cast(dict[str, object], initial_status["backlog"])

    first = foundry.sources.start_debezium_object_index(
        "orders_cdc",
        object_type_api_name="Order",
        idempotency_key="source-cdc-index-1",
        max_rows_per_version=10,
        ctx=ctx,
    )
    second = foundry.sources.start_debezium_object_index(
        "orders_cdc",
        object_type_api_name="Order",
        idempotency_key="source-cdc-index-2",
        max_rows_per_version=10,
        ctx=ctx,
    )
    third = foundry.sources.start_debezium_object_index(
        "orders_cdc",
        object_type_api_name="Order",
        idempotency_key="source-cdc-index-3",
        max_rows_per_version=10,
        ctx=ctx,
    )

    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    first_cursor = cast(dict[str, object], first["cursor"])
    first_backlog = cast(dict[str, object], first["backlog"])
    second_backlog = cast(dict[str, object], second["backlog"])
    third_backlog = cast(dict[str, object], third["backlog"])
    third_cursor = cast(dict[str, object], third["cursor"])
    caught_up_plan = foundry.sources.debezium_operation_plan("orders_cdc", object_type_api_name="Order", ctx=ctx)
    caught_up_status = cast(dict[str, object], caught_up_plan["objectIndexingStatus"])
    caught_up_backlog = cast(dict[str, object], caught_up_status["backlog"])

    assert initial_status["workflowRunId"] is None
    assert capture_semantics == {
        "snapshotMode": "initial_then_logical_changes",
        "snapshotResponsibility": "external_debezium_connector",
        "operationCodes": ["r", "c", "u", "d"],
        "primaryKey": ["order_id"],
        "ordering": "topic_partition_offset",
        "deletePolicy": "tombstone",
        "archiveShape": "append_only_cdc_envelope",
    }
    assert initial_status["lastIndexedVersionNumber"] is None
    assert initial_backlog["remainingVersionCount"] == 2
    assert initial_backlog["nextSourceDatasetVersionNumber"] == 1
    assert initial_status["nextAction"] == "run_object_index_again"
    assert first["status"] == "INDEXED"
    assert first["eventCount"] == 1
    assert str(first["indexRunId"]).startswith("index_run_")
    assert str(first["operationsPath"]).startswith("/api/operations/runs/index/index_run_")
    assert str(first["workflowOperationPath"]).startswith("/api/operations/runs/workflow/")
    assert first_cursor["lastIndexedVersionNumber"] == 1
    assert first_backlog["remainingVersionCount"] == 1
    assert first_backlog["hasMoreVersions"] is True
    assert first_backlog["nextSourceDatasetVersionNumber"] == 2
    assert first["nextAction"] == "run_object_index_again"
    assert second["status"] == "INDEXED"
    assert second_backlog["remainingVersionCount"] == 0
    assert second["nextAction"] == "monitor_operations"
    assert third["status"] == "NO_VERSIONS"
    assert third["operationsPath"] is None
    assert third_backlog["remainingVersionCount"] == 0
    assert third["nextAction"] == "wait_for_next_cdc_version"
    assert third_cursor["lastIndexedVersionNumber"] == 2
    assert caught_up_status["workflowRunId"] == third["workflowRunId"]
    assert caught_up_status["workflowOperationPath"] == third["workflowOperationPath"]
    assert caught_up_status["lastIndexedVersionNumber"] == 2
    assert caught_up_status["lastIndexRunId"] == second["indexRunId"]
    assert caught_up_backlog["remainingVersionCount"] == 0
    assert caught_up_status["nextAction"] == "monitor_operations"
    assert order["properties"]["status"] == "SHIPPED"


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


def test_managed_db_sync_empty_poll_preserves_checkpoint_and_avoids_reingest(tmp_path: Path) -> None:
    """A caught-up incremental tick must not reset the checkpoint or re-ingest.

    The database adapter returns an empty checkpoint when a batch has no rows, and
    a caught-up APPEND sync normally reads zero new rows. Persisting that empty
    checkpoint reset the resume cursor to None, so the following tick re-read the
    whole table and appended every row again as duplicates. The prior checkpoint
    must survive an empty poll.
    """
    source_db = _sqlite_customer_erp(tmp_path)  # ids 1,2,3
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    secret_ref = _create_source_database_secret(foundry, source_db, ctx)

    foundry.sources.create_managed_sync(
        sync_name="orders_incremental_empty_poll",
        source_name="customer_erp_empty_poll",
        display_name="Orders incremental empty poll",
        source_type="postgres_jdbc",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.empty_poll_orders",
        schedule={"mode": "manual"},
        config_summary={
            "databaseUrlSecretRef": secret_ref["name"],
            "tableName": "orders",
            "checkpointColumn": "id",
            "batchLimit": 10,
        },
        idempotency_key="source-sync-empty-poll",
        ctx=ctx,
    )

    first = foundry.sources.start_managed_sync_run(
        "orders_incremental_empty_poll", idempotency_key="empty-poll-run-1", batch_limit=10, ctx=ctx
    )
    assert first["status"] == "succeeded"
    assert first["checkpointEnd"] == {"checkpointColumn": "id", "lastValue": 3}
    assert len(foundry.datasets.preview("raw.empty_poll_orders", ctx=ctx)) == 3

    # Second tick reads no new rows. The checkpoint must carry forward, not reset.
    second = foundry.sources.start_managed_sync_run(
        "orders_incremental_empty_poll", idempotency_key="empty-poll-run-2", batch_limit=10, ctx=ctx
    )
    assert second["status"] == "succeeded"
    assert second["checkpointEnd"] == {"checkpointColumn": "id", "lastValue": 3}

    # Third tick proves the resume cursor was preserved: no rows are re-appended.
    third = foundry.sources.start_managed_sync_run(
        "orders_incremental_empty_poll", idempotency_key="empty-poll-run-3", batch_limit=10, ctx=ctx
    )
    assert third["status"] == "succeeded"
    assert len(foundry.datasets.preview("raw.empty_poll_orders", ctx=ctx)) == 3


def _foundry(tmp_path: Path) -> FoundryLite:
    return FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))


def _seed_order_cdc_source(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    snapshot = tmp_path / "orders.csv"
    snapshot.write_text("order_id,status,amount\nO-1001,PENDING,700\n", encoding="utf-8")
    foundry.datasets.upload_csv("clean.orders", snapshot, ctx=ctx)
    foundry.ontology.apply(str(_order_cdc_ontology(tmp_path)), ctx=ctx)


def _commit_cdc_version(foundry: FoundryLite, tmp_path: Path, event_id: str, lsn: int, status: str) -> None:
    path = tmp_path / f"{event_id.replace(':', '_')}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_id", "op", "pk_json", "after_json", "ordering_json"])
        writer.writeheader()
        writer.writerow(
            {
                "event_id": event_id,
                "op": "u",
                "pk_json": json.dumps({"order_id": "O-1001"}, sort_keys=True),
                "after_json": json.dumps(
                    {"order_id": "O-1001", "status": status, "amount": 700 + lsn},
                    sort_keys=True,
                ),
                "ordering_json": json.dumps(
                    {"lsn": lsn, "offset": lsn, "source_ts_ms": 1700000000000 + lsn, "table": "orders"},
                    sort_keys=True,
                ),
            }
        )
    foundry.datasets.upload_csv("raw_cdc.erp_orders", path, ctx=demo_admin_context())


def _order_cdc_ontology(tmp_path: Path) -> Path:
    path = tmp_path / "order-cdc-source.yaml"
    path.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
      cdc:
        dataset: raw_cdc.erp_orders
        primaryKeyColumns: [order_id]
        deletePolicy: tombstone
    properties:
      - apiName: orderId
        column: order_id
        type: string
        indexed: true
        nullable: false
      - apiName: status
        column: status
        type: string
        indexed: true
      - apiName: amount
        column: amount
        type: float
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _create_source_database_secret(foundry: FoundryLite, source_db: Path, ctx) -> dict[str, object]:
    credential = foundry.sources.create_credential(
        credential_name=f"erp_db_{source_db.stem}",
        display_name="Customer ERP DB",
        kind="postgres_jdbc",
        auth_scheme="database_url",
        secret_value=f"sqlite:///{source_db}",
        idempotency_key=f"source-credential-{source_db.stem}",
        ctx=ctx,
    )
    return cast(dict[str, object], credential["secretRef"])


def _minute_slot() -> str:
    return datetime.now(UTC).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _decision_for(result: Mapping[str, object], sync_name: str) -> dict[str, object]:
    decisions = cast(list[dict[str, object]], result["decisions"])
    return next(item for item in decisions if item["syncName"] == sync_name)


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
