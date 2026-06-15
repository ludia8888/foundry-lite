from __future__ import annotations

from uuid import uuid4

import pytest
from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, insert, select, text
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
    assert _visible_values(engine, role_name, "tenant-demo", db.datasets.c.id) == ["dataset-demo"]
    assert _visible_values(engine, role_name, "tenant-demo", db.object_records.c.id) == ["object-demo"]
    assert _visible_values(engine, role_name, "tenant-demo", db.dataset_schemas.c.id) == ["schema-demo"]
    assert _visible_values(engine, role_name, "tenant-other", db.datasets.c.id) == ["dataset-other"]
    assert _visible_values(engine, role_name, "tenant-other", db.object_records.c.id) == ["object-other"]
    assert _visible_values(engine, role_name, "tenant-other", db.dataset_schemas.c.id) == ["schema-other"]
    assert _visible_without_tenant_context(engine, role_name, db.datasets.c.id) == []
    _assert_cross_tenant_insert_is_rejected(engine, role_name)


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


def _rls_enabled(engine: Engine, table_name: str) -> bool:
    with engine.begin() as conn:
        enabled = conn.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :table_name"),
            {"table_name": table_name},
        ).scalar_one()
    return bool(enabled)


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
