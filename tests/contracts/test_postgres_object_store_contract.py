from __future__ import annotations

import json
from uuid import uuid4

import pytest
from foundry_lite.infrastructure import schema as db
from sqlalchemy import insert, inspect, text
from sqlalchemy.exc import SQLAlchemyError

_OBJECT_STORE_TABLES = (
    db.object_records,
    db.object_record_versions,
    db.object_links,
    db.object_edits,
    db.object_conflicts,
    db.object_sets,
    db.object_change_counters,
    db.object_index_row_hashes,
    db.object_index_versions,
)

_JSONB_COLUMNS = {
    "object_records": {"properties", "base_properties", "edit_properties", "property_versions"},
    "object_record_versions": {"properties", "base_properties", "edit_properties", "property_versions"},
    "object_links": {"properties"},
    "object_edits": {"patch", "previous_values", "revert_payload"},
    "object_conflicts": {"source_value", "edit_value"},
    "object_sets": {"definition"},
}


def test_postgres_object_documents_are_jsonb_with_declared_production_indexes(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    inspector = inspect(engine)

    for table_name, expected_columns in _JSONB_COLUMNS.items():
        columns = {column["name"]: str(column["type"]).upper() for column in inspector.get_columns(table_name)}
        assert {name for name in expected_columns if columns[name] == "JSONB"} == expected_columns

    record_indexes = {index["name"] for index in inspector.get_indexes("object_records")}
    link_indexes = {index["name"] for index in inspector.get_indexes("object_links")}
    assert {
        "ix_object_records_serving_lookup",
        "ix_object_records_type_version",
        "ix_object_records_change_sequence",
        "ix_object_records_properties_gin",
    } <= record_indexes
    assert {
        "ix_object_links_from_active",
        "ix_object_links_to_active",
        "ix_object_links_properties_gin",
    } <= link_indexes


def test_postgres_jsonb_containment_plan_can_use_the_gin_index_at_scale(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO object_records (
                  id, tenant_id, object_type_id, object_type_api_name, object_id,
                  index_version, is_active, properties, base_properties, edit_properties,
                  property_versions, source_dataset_version_id, source_hash, object_version,
                  object_change_sequence, deleted, deletion_reason, created_at, updated_at
                )
                SELECT
                  'obj-' || value, 'tenant-demo', 'ot-order', 'Order', 'O-' || value,
                  'active', true,
                  jsonb_build_object(
                    'status', CASE WHEN value % 1000 = 0 THEN 'MATCH' ELSE 'OTHER' END,
                    'amount', value,
                    'desk', 'desk-' || (value % 20)
                  ),
                  jsonb_build_object('status', 'OTHER', 'amount', value),
                  '{}'::jsonb,
                  jsonb_build_object('status', 1, 'amount', 1),
                  'dsv-orders', 'hash-' || value, 1, value, false, NULL,
                  '2026-08-18T00:00:00Z', '2026-08-18T00:00:00Z'
                FROM generate_series(1, 20000) AS value
                """
            )
        )
        connection.execute(text("ANALYZE object_records"))
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = connection.execute(
            text(
                """
                EXPLAIN (FORMAT JSON, COSTS OFF)
                SELECT object_id
                FROM object_records
                WHERE properties @> '{"status":"MATCH"}'::jsonb
                """
            )
        ).scalar_one()
        count = connection.execute(
            text('SELECT count(*) FROM object_records WHERE properties @> \'{"status":"MATCH"}\'::jsonb')
        ).scalar_one()

    assert count == 20
    assert "ix_object_records_properties_gin" in json.dumps(plan)


def test_all_postgres_object_store_tables_force_tenant_rls(postgres_fixture) -> None:
    table_names = [table.name for table in _OBJECT_STORE_TABLES]
    with postgres_fixture.engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       EXISTS (
                         SELECT 1 FROM pg_policies p
                         WHERE p.schemaname = current_schema()
                           AND p.tablename = c.relname
                           AND p.policyname = c.relname || '_tenant_isolation'
                       ) AS has_tenant_policy
                FROM pg_class c
                WHERE c.relname = ANY(:table_names)
                ORDER BY c.relname
                """
            ),
            {"table_names": table_names},
        ).mappings()

    evidence = {str(row["relname"]): row for row in rows}
    assert set(evidence) == set(table_names)
    assert all(row["relrowsecurity"] is True for row in evidence.values())
    assert all(row["relforcerowsecurity"] is True for row in evidence.values())
    assert all(row["has_tenant_policy"] is True for row in evidence.values())


def test_all_postgres_object_store_tables_enforce_rls_for_non_superuser(postgres_fixture) -> None:
    engine = postgres_fixture.engine
    role_name = f"foundry_lite_object_store_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role_name}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role_name}"'))
        connection.execute(
            text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role_name}"')
        )
        connection.execute(text(f'GRANT "{role_name}" TO CURRENT_USER'))
        for table, row in _object_store_rows("demo", "tenant-rls-demo"):
            connection.execute(insert(table), row)
        for table, row in _object_store_rows("other", "tenant-rls-other"):
            connection.execute(insert(table), row)

    for table in _OBJECT_STORE_TABLES:
        assert _visible_tenants(engine, role_name, table.name, "tenant-rls-demo") == ["tenant-rls-demo"]
        assert _visible_tenants(engine, role_name, table.name, "tenant-rls-other") == ["tenant-rls-other"]
        assert _visible_tenants(engine, role_name, table.name, None) == []
    _assert_cross_tenant_writes_are_blocked(engine, role_name)


def test_jsonb_index_contract_is_declared_in_sqlalchemy_metadata() -> None:
    assert "ix_object_records_properties_gin" in {index.name for index in db.object_records.indexes}


def _visible_tenants(engine, role_name: str, table_name: str, tenant_id: str | None) -> list[str]:
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL ROLE "{role_name}"'))
        if tenant_id is not None:
            connection.execute(
                text("SELECT set_config('foundry_lite.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
        return list(connection.execute(text(f"SELECT tenant_id FROM {table_name} ORDER BY tenant_id")).scalars())


def _assert_cross_tenant_writes_are_blocked(engine, role_name: str) -> None:
    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(text(f'SET LOCAL ROLE "{role_name}"'))
        connection.execute(text("SELECT set_config('foundry_lite.tenant_id', 'tenant-rls-demo', true)"))
        connection.execute(
            text("INSERT INTO object_change_counters (tenant_id, last_sequence) VALUES ('tenant-rls-blocked', 1)")
        )


def _object_store_rows(suffix: str, tenant_id: str):
    timestamp = "2026-08-18T00:00:00Z"
    return (
        (
            db.object_records,
            {
                "id": f"record-{suffix}",
                "tenant_id": tenant_id,
                "object_type_id": "ot-order",
                "object_type_api_name": "Order",
                "object_id": f"order-{suffix}",
                "index_version": "active",
                "is_active": True,
                "properties": {"status": suffix},
                "base_properties": {"status": suffix},
                "edit_properties": {},
                "property_versions": {"status": 1},
                "object_version": 1,
                "object_change_sequence": 1,
                "deleted": False,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        ),
        (
            db.object_record_versions,
            {
                "id": f"record-version-{suffix}",
                "tenant_id": tenant_id,
                "object_record_id": f"record-{suffix}",
                "object_type_id": "ot-order",
                "object_type_api_name": "Order",
                "object_id": f"order-{suffix}",
                "index_version": "active",
                "is_active": True,
                "properties": {"status": suffix},
                "base_properties": {"status": suffix},
                "edit_properties": {},
                "property_versions": {"status": 1},
                "object_version": 1,
                "object_change_sequence": 1,
                "deleted": False,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        ),
        (
            db.object_links,
            {
                "id": f"link-{suffix}",
                "tenant_id": tenant_id,
                "link_type_id": "lt-order-customer",
                "link_type_api_name": "OrderCustomer",
                "index_version": "active",
                "is_active": True,
                "from_object_type_id": "ot-order",
                "from_api_name": "Order",
                "from_object_id": f"order-{suffix}",
                "to_object_type_id": "ot-customer",
                "to_api_name": "Customer",
                "to_object_id": f"customer-{suffix}",
                "properties": {"kind": suffix},
                "link_version": 1,
                "deleted": False,
                "updated_at": timestamp,
            },
        ),
        (
            db.object_edits,
            {
                "id": f"edit-{suffix}",
                "tenant_id": tenant_id,
                "object_type_id": "ot-order",
                "object_type_api_name": "Order",
                "object_id": f"order-{suffix}",
                "edit_type": "update",
                "patch": {"status": suffix},
                "previous_values": {"status": "before"},
                "created_at": timestamp,
            },
        ),
        (
            db.object_conflicts,
            {
                "id": f"conflict-{suffix}",
                "tenant_id": tenant_id,
                "object_type_id": "ot-order",
                "object_id": f"order-{suffix}",
                "property_api_name": "status",
                "source_value": "before",
                "edit_value": suffix,
                "status": "open",
                "created_at": timestamp,
            },
        ),
        (
            db.object_sets,
            {
                "id": f"set-{suffix}",
                "tenant_id": tenant_id,
                "name": f"Orders {suffix}",
                "object_type_id": "ot-order",
                "set_type": "static",
                "definition": {"objectIds": [f"order-{suffix}"]},
                "visibility": "private",
                "created_at": timestamp,
            },
        ),
        (db.object_change_counters, {"tenant_id": tenant_id, "last_sequence": 1}),
        (
            db.object_index_row_hashes,
            {
                "id": f"row-hash-{suffix}",
                "tenant_id": tenant_id,
                "object_type_id": "ot-order",
                "object_id": f"order-{suffix}",
                "row_hash": f"sha256:{suffix}",
                "dataset_version_id": f"dataset-version-{suffix}",
                "updated_at": timestamp,
            },
        ),
        (
            db.object_index_versions,
            {
                "id": f"index-version-{suffix}",
                "tenant_id": tenant_id,
                "object_type_id": "ot-order",
                "active_index_version": "active",
                "updated_at": timestamp,
            },
        ),
    )
