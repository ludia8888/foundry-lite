from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.pipeline_preview_recovery import (
    PipelinePreviewRecoveryCursor,
    recoverable_pipeline_previews,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.postgres_rls import install_postgres_rls_tenant_context
from foundry_lite.infrastructure.repositories.metadata_repository import (
    SqlAlchemyMetadataRepository,
)
from foundry_lite.infrastructure.repositories.pipeline_execution_repository import (
    SqlAlchemyPipelineExecutionRepository,
)
from foundry_lite.security.tenant_context import tenant_context
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError


def test_postgres_rls_hides_dataset_and_object_rows_between_tenants(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    role_name = f"foundry_lite_rls_test_{uuid4().hex}"
    _grant_rls_role(engine, role_name)
    _seed_cross_tenant_rows(engine)

    assert _rls_enabled(engine, db.datasets.name)
    assert _rls_enabled(engine, db.object_records.name)
    assert _rls_enabled(engine, db.dataset_schemas.name)
    assert _rls_forced(engine, db.datasets.name)
    assert _rls_forced(engine, db.object_records.name)
    assert _rls_forced(engine, db.dataset_schemas.name)
    assert _visible_values(engine, role_name, "tenant-demo", db.datasets.c.id) == ["dataset-demo"]
    assert _visible_values(engine, role_name, "tenant-demo", db.object_records.c.id) == ["object-demo"]
    assert _visible_values(engine, role_name, "tenant-demo", db.dataset_schemas.c.id) == ["schema-demo"]
    assert _visible_values(engine, role_name, "tenant-other", db.datasets.c.id) == ["dataset-other"]
    assert _visible_values(engine, role_name, "tenant-other", db.object_records.c.id) == ["object-other"]
    assert _visible_values(engine, role_name, "tenant-other", db.dataset_schemas.c.id) == ["schema-other"]
    assert _visible_without_tenant_context(engine, role_name, db.datasets.c.id) == []
    _assert_cross_tenant_insert_is_rejected(engine, role_name)


def test_postgres_rls_hides_action_execution_ledger_between_tenants(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    role_name = f"foundry_lite_action_rls_{uuid4().hex}"
    _grant_rls_role(engine, role_name)
    _seed_action_execution_rows(engine)

    tables = (
        db.action_runs,
        db.action_run_steps,
        db.action_step_attempts,
        db.action_run_events,
        db.action_effect_receipts,
        db.action_log_entries,
        db.action_log_objects,
    )
    for table in tables:
        assert _rls_enabled(engine, table.name)
        assert _rls_forced(engine, table.name)
        assert _visible_values(engine, role_name, "tenant-demo", table.c.id) == [f"{table.name}-demo"]
        assert _visible_values(engine, role_name, "tenant-other", table.c.id) == [f"{table.name}-other"]
        assert _visible_without_tenant_context(engine, role_name, table.c.id) == []


def test_rls_tenant_context_reset_between_pooled_connections(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    pooled_engine = create_engine(engine.url, future=True, pool_size=1, max_overflow=0)
    role_name = f"foundry_lite_rls_pool_test_{uuid4().hex}"
    _grant_rls_role(engine, role_name)
    _seed_cross_tenant_rows(engine)

    try:
        demo_pid, demo_rows = _visible_dataset_ids_on_pooled_connection(pooled_engine, role_name, "tenant-demo")
        no_tenant_pid, no_tenant_rows = _visible_dataset_ids_without_tenant_on_pooled_connection(
            pooled_engine, role_name
        )
        other_pid, other_rows = _visible_dataset_ids_on_pooled_connection(pooled_engine, role_name, "tenant-other")
    finally:
        pooled_engine.dispose()

    assert demo_pid == no_tenant_pid == other_pid
    assert demo_rows == ["dataset-demo"]
    assert no_tenant_rows == []
    assert other_rows == ["dataset-other"]


def test_installed_rls_hook_uses_current_request_tenant(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    pooled_engine = create_engine(engine.url, future=True, pool_size=1, max_overflow=0)
    install_postgres_rls_tenant_context(pooled_engine)
    role_name = f"foundry_lite_rls_hook_test_{uuid4().hex}"
    _grant_rls_role(engine, role_name)
    _seed_cross_tenant_rows(engine)

    try:
        with tenant_context("tenant-demo"):
            demo_pid, demo_rows = _visible_dataset_ids_with_role_only(pooled_engine, role_name)
        no_tenant_pid, no_tenant_rows = _visible_dataset_ids_with_role_only(pooled_engine, role_name)
        with tenant_context("tenant-other"):
            other_pid, other_rows = _visible_dataset_ids_with_role_only(pooled_engine, role_name)
    finally:
        pooled_engine.dispose()

    assert demo_pid == no_tenant_pid == other_pid
    assert demo_rows == ["dataset-demo"]
    assert no_tenant_rows == []
    assert other_rows == ["dataset-other"]


def test_preview_recovery_enumerates_tenants_and_binds_rls_context(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    role_name = f"foundry_lite_preview_recovery_{uuid4().hex}"
    _grant_rls_role(engine, role_name)
    _seed_cross_tenant_rows(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(db.pipeline_preview_runs),
            [
                _preview_row("preview-demo", "tenant-demo"),
                _preview_row("preview-other", "tenant-other"),
            ],
        )

    worker_engine = create_engine(engine.url, future=True, pool_size=1, max_overflow=0)
    install_postgres_rls_tenant_context(worker_engine)
    event.listen(worker_engine, "begin", _set_rls_test_role(role_name))
    try:
        rows = recoverable_pipeline_previews(
            worker_engine,
            SqlAlchemyPipelineExecutionRepository(worker_engine),
            SqlAlchemyMetadataRepository(worker_engine, allow_schema_mutation=False),
            PipelinePreviewRecoveryCursor(),
            as_of="2026-07-28T00:00:00.000000Z",
            limit=10,
        )
    finally:
        worker_engine.dispose()

    assert _rls_forced(engine, db.pipeline_preview_runs.name)
    assert {(row["tenant_id"], row["id"]) for row in rows} == {
        ("tenant-demo", "preview-demo"),
        ("tenant-other", "preview-other"),
    }


def test_control_tick_recovers_crashed_running_run_under_rls(postgres_fixture, tmp_path: Path) -> None:
    engine = postgres_fixture.engine
    role_name = f"foundry_lite_control_recovery_{uuid4().hex}"
    _grant_rls_role(engine, role_name)

    # Build the control worker on its own engine. Bootstrap runs as the container
    # superuser (before the role listener downgrades the connection), so schema
    # DDL and demo seeding succeed; only the later tick() runs under the role.
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=engine.url.render_as_string(hide_password=False),
            storage_root=tmp_path / "control-worker",
        )
    )
    # A crashed run: still "running" with an execution lease whose expiry is long
    # past. Seeded as superuser so it lands regardless of RLS.
    with engine.begin() as conn:
        conn.execute(insert(db.pipeline_runs), [_crashed_running_run("run-crashed", "tenant-demo")])

    # From here the worker's connections run as the dedicated non-superuser role,
    # so FORCE ROW LEVEL SECURITY applies exactly as it does in production.
    event.listen(foundry.engine, "begin", _set_rls_test_role(role_name))
    try:
        totals = foundry._services.pipelines.control.tick(limit=100)
    finally:
        cast(Engine, foundry.engine).dispose()

    assert _rls_forced(engine, db.pipeline_runs.name)
    # The tenant-blind scan (tenant_id=None, no bound context) would see zero rows
    # under the role and recover nothing; enumerating tenants + binding context
    # makes the crashed run visible and fails it closed.
    assert totals["staleExecutions"] == 1
    assert _run_status(engine, "run-crashed") == "failed"


def _grant_rls_role(engine: Engine, role_name: str) -> None:
    with engine.begin() as conn:
        role = _role_identifier(conn, role_name)
        conn.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
        conn.execute(text(f"GRANT {role} TO CURRENT_USER"))


def _seed_cross_tenant_rows(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.tenants),
            [{"id": "tenant-other", "name": "Other", "created_at": "2026-06-12T00:00:00Z"}],
        )
        conn.execute(
            insert(db.datasets),
            [_dataset_row("dataset-demo", "tenant-demo"), _dataset_row("dataset-other", "tenant-other")],
        )
        conn.execute(
            insert(db.dataset_schemas),
            [_schema_row("schema-demo", "dataset-demo"), _schema_row("schema-other", "dataset-other")],
        )
        conn.execute(
            insert(db.object_records),
            [_object_row("object-demo", "tenant-demo"), _object_row("object-other", "tenant-other")],
        )


def _seed_action_execution_rows(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.action_runs),
            [_action_run_row("demo", "tenant-demo"), _action_run_row("other", "tenant-other")],
        )
        conn.execute(
            insert(db.action_run_steps),
            [_action_step_row("demo", "tenant-demo"), _action_step_row("other", "tenant-other")],
        )
        conn.execute(
            insert(db.action_step_attempts),
            [_action_attempt_row("demo", "tenant-demo"), _action_attempt_row("other", "tenant-other")],
        )
        conn.execute(
            insert(db.action_run_events),
            [_action_event_row("demo", "tenant-demo"), _action_event_row("other", "tenant-other")],
        )
        conn.execute(
            insert(db.action_effect_receipts),
            [_action_effect_row("demo", "tenant-demo"), _action_effect_row("other", "tenant-other")],
        )
        conn.execute(
            insert(db.action_log_entries),
            [_action_log_row("demo", "tenant-demo"), _action_log_row("other", "tenant-other")],
        )
        conn.execute(
            insert(db.action_log_objects),
            [_action_log_object_row("demo", "tenant-demo"), _action_log_object_row("other", "tenant-other")],
        )


def _action_run_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_runs-{suffix}",
        "tenant_id": tenant_id,
        "action_type_id": f"action-{suffix}",
        "action_type_api_name": "ApproveOrder",
        "actor_user_id": f"user-{suffix}",
        "target_object_type_id": f"order-type-{suffix}",
        "target_object_type_api_name": "Order",
        "target_object_id": f"order-{suffix}",
        "expected_object_version": 1,
        "parameters": {},
        "status": "running",
        "idempotency_key": f"action-key-{suffix}",
        "request_fingerprint": f"request-{suffix}",
        "execution_mode": "async",
        "dispatch_status": "dispatched",
        "dispatch_attempt_count": 1,
        "event_sequence": 1,
        "created_at": "2026-08-03T00:00:00Z",
    }


def _action_step_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_run_steps-{suffix}",
        "tenant_id": tenant_id,
        "run_id": f"action_runs-{suffix}",
        "step_key": "function",
        "step_kind": "function",
        "status": "running",
        "attempt_count": 1,
        "input_manifest": {},
        "output_manifest": {},
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    }


def _action_attempt_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_step_attempts-{suffix}",
        "tenant_id": tenant_id,
        "step_id": f"action_run_steps-{suffix}",
        "attempt_number": 1,
        "status": "running",
        "worker_id": f"worker-{suffix}",
        "lease_token": f"lease-{suffix}",
        "lease_expires_at": "2026-08-03T00:05:00Z",
        "fencing_token": 1,
        "heartbeat_at": "2026-08-03T00:00:00Z",
        "input_manifest": {},
        "output_manifest": {},
        "started_at": "2026-08-03T00:00:00Z",
    }


def _action_event_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_run_events-{suffix}",
        "tenant_id": tenant_id,
        "run_id": f"action_runs-{suffix}",
        "sequence": 1,
        "event_type": "action.step.running",
        "payload": {},
        "created_at": "2026-08-03T00:00:00Z",
    }


def _action_effect_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_effect_receipts-{suffix}",
        "tenant_id": tenant_id,
        "action_run_id": f"action_runs-{suffix}",
        "effect_id": "notify",
        "phase": "after_commit",
        "effect_kind": "event",
        "target_ref": "topic:orders",
        "status": "pending",
        "idempotency_key": f"effect-key-{suffix}",
        "attempt_count": 0,
        "max_attempts": 3,
        "fencing_token": 0,
        "request": {},
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    }


def _action_log_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_log_entries-{suffix}",
        "tenant_id": tenant_id,
        "action_run_id": f"action_runs-{suffix}",
        "log_object_type_api_name": "[LOG] ApproveOrder",
        "log_object_id": f"action_runs-{suffix}",
        "action_type_id": f"action-{suffix}",
        "action_type_api_name": "ApproveOrder",
        "definition_version": f"definition-{suffix}",
        "actor_user_id": f"user-{suffix}",
        "status": "succeeded",
        "parameters": {},
        "result": {},
        "revert_allowed": True,
        "revert_status": "eligible",
        "created_at": "2026-08-03T00:00:00Z",
        "completed_at": "2026-08-03T00:00:01Z",
    }


def _action_log_object_row(suffix: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": f"action_log_objects-{suffix}",
        "tenant_id": tenant_id,
        "action_log_entry_id": f"action_log_entries-{suffix}",
        "object_edit_id": f"object-edit-{suffix}",
        "object_type_id": f"order-type-{suffix}",
        "object_type_api_name": "Order",
        "object_id": f"order-{suffix}",
        "edit_type": "set_property",
        "ordinal": 0,
    }


def _dataset_row(dataset_id: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": dataset_id,
        "tenant_id": tenant_id,
        "namespace": "clean",
        "name": "orders",
        "description": None,
        "storage_kind": "local",
        "storage_uri": None,
        "owner_team": None,
        "classification": None,
        "status": "active",
        "primary_key": ["order_id"],
        "partition_spec": [],
        "sort_order": [],
        "target_file_size_bytes": None,
        "created_at": "2026-06-12T00:00:00Z",
        "updated_at": "2026-06-12T00:00:00Z",
    }


def _schema_row(schema_id: str, dataset_id: str) -> dict[str, object]:
    return {
        "id": schema_id,
        "dataset_id": dataset_id,
        "version": 1,
        "schema_json": {"columns": ["order_id"]},
        "schema_hash": f"{schema_id}-hash",
        "created_at": "2026-06-12T00:00:00Z",
    }


def _object_row(row_id: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": row_id,
        "tenant_id": tenant_id,
        "object_type_id": f"order-type-{tenant_id}",
        "object_type_api_name": "Order",
        "object_id": f"order-{tenant_id}",
        "index_version": "active",
        "is_active": True,
        "properties": {"order_id": f"order-{tenant_id}"},
        "base_properties": {"order_id": f"order-{tenant_id}"},
        "edit_properties": {},
        "property_versions": {},
        "source_dataset_version_id": None,
        "source_hash": None,
        "object_version": 1,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-12T00:00:00Z",
        "updated_at": "2026-06-12T00:00:00Z",
    }


def _preview_row(row_id: str, tenant_id: str) -> dict[str, object]:
    return {
        "id": row_id,
        "tenant_id": tenant_id,
        "pipeline_id": f"pipeline-{tenant_id}",
        "branch_id": f"branch-{tenant_id}",
        "status": "QUEUED",
        "graph": {"schemaVersion": 2, "nodes": [], "edges": []},
        "graph_fingerprint": f"graph-{tenant_id}",
        "target_node_id": None,
        "limits": {},
        "outputs": [],
        "artifacts": [],
        "idempotency_key": f"preview-key-{tenant_id}",
        "request_fingerprint": f"request-{tenant_id}",
        "is_commit_forbidden": True,
        "execution_context": {"actorUserId": f"user-{tenant_id}", "roles": ["data_engineer"]},
        "execution_lease_token": None,
        "execution_lease_expires_at": None,
        "execution_heartbeat_at": None,
        "cancel_requested_at": None,
        "error": None,
        "created_by": f"user-{tenant_id}",
        "created_at": "2026-07-28T00:00:00.000000Z",
        "started_at": None,
        "completed_at": None,
    }


def _crashed_running_run(run_id: str, tenant_id: str) -> dict[str, object]:
    # A run whose worker crashed mid-execution: still "running", holding an
    # execution lease whose expiry is far in the past so stale recovery reclaims it.
    return {
        "id": run_id,
        "tenant_id": tenant_id,
        "pipeline_id": f"pipeline-{tenant_id}",
        "version_id": f"version-{tenant_id}",
        "status": "running",
        "idempotency_key": f"run-key-{run_id}",
        "request_fingerprint": f"request-{run_id}",
        "plan_fingerprint": f"plan-{run_id}",
        "execution_lease_token": "lease-crashed",
        "execution_lease_expires_at": "2000-01-01T00:00:00.000000Z",
        "execution_heartbeat_at": "2000-01-01T00:00:00.000000Z",
        "outputs": [],
        "timeline": [{"event": "pipeline.run.execution_claimed", "at": "2000-01-01T00:00:00.000000Z"}],
        "created_by": f"user-{tenant_id}",
        "started_at": "2000-01-01T00:00:00.000000Z",
    }


def _run_status(engine: Engine, run_id: str) -> str:
    with engine.begin() as conn:
        return str(conn.execute(select(db.pipeline_runs.c.status).where(db.pipeline_runs.c.id == run_id)).scalar_one())


def _set_rls_test_role(role_name: str):
    def set_role(conn: Connection) -> None:
        conn.execute(text(f"SET LOCAL ROLE {_role_identifier(conn, role_name)}"))

    return set_role


def _rls_enabled(engine: Engine, table_name: str) -> bool:
    with engine.begin() as conn:
        enabled = conn.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :table_name"),
            {"table_name": table_name},
        ).scalar_one()
    return bool(enabled)


def _rls_forced(engine: Engine, table_name: str) -> bool:
    with engine.begin() as conn:
        forced = conn.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :table_name"),
            {"table_name": table_name},
        ).scalar_one()
    return bool(forced)


def _visible_values(engine: Engine, role_name: str, tenant_id: str, column) -> list[str]:
    with engine.begin() as conn:
        _set_role_and_tenant(conn, role_name, tenant_id)
        values = conn.execute(select(column).order_by(column)).scalars().all()
    return [str(value) for value in values]


def _visible_dataset_ids_on_pooled_connection(engine: Engine, role_name: str, tenant_id: str) -> tuple[int, list[str]]:
    with engine.begin() as conn:
        _set_role_and_tenant(conn, role_name, tenant_id)
        return _backend_pid(conn), _dataset_ids(conn)


def _visible_dataset_ids_without_tenant_on_pooled_connection(engine: Engine, role_name: str) -> tuple[int, list[str]]:
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {_role_identifier(conn, role_name)}"))
        return _backend_pid(conn), _dataset_ids(conn)


def _visible_dataset_ids_with_role_only(engine: Engine, role_name: str) -> tuple[int, list[str]]:
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {_role_identifier(conn, role_name)}"))
        return _backend_pid(conn), _dataset_ids(conn)


def _visible_without_tenant_context(engine: Engine, role_name: str, column) -> list[str]:
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {_role_identifier(conn, role_name)}"))
        values = conn.execute(select(column).order_by(column)).scalars().all()
    return [str(value) for value in values]


def _backend_pid(conn: Connection) -> int:
    return int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())


def _dataset_ids(conn: Connection) -> list[str]:
    values = conn.execute(select(db.datasets.c.id).order_by(db.datasets.c.id)).scalars().all()
    return [str(value) for value in values]


def _assert_cross_tenant_insert_is_rejected(engine: Engine, role_name: str) -> None:
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            _set_role_and_tenant(conn, role_name, "tenant-demo")
            with pytest.raises(SQLAlchemyError, match="row-level security"):
                conn.execute(insert(db.object_records).values(_object_row("object-cross", "tenant-other")))
        finally:
            transaction.rollback()


def _set_role_and_tenant(conn: Connection, role_name: str, tenant_id: str) -> None:
    conn.execute(text(f"SET LOCAL ROLE {_role_identifier(conn, role_name)}"))
    db.set_postgres_tenant_context(conn, tenant_id)


def _role_identifier(conn: Connection, role_name: str) -> str:
    return conn.dialect.identifier_preparer.quote(role_name)
