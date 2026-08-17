"""Verify the live PostgreSQL object-store contract from a protected runtime Pod."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any

import psycopg
from psycopg.rows import dict_row

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,100}$")
_OBJECT_STORE_TABLES = (
    "object_records",
    "object_record_versions",
    "object_links",
    "object_edits",
    "object_conflicts",
    "object_sets",
    "object_change_counters",
    "object_index_row_hashes",
    "object_index_versions",
)
_JSONB_COLUMNS = frozenset(
    {
        ("object_records", "properties"),
        ("object_records", "base_properties"),
        ("object_records", "edit_properties"),
        ("object_records", "property_versions"),
        ("object_record_versions", "properties"),
        ("object_record_versions", "base_properties"),
        ("object_record_versions", "edit_properties"),
        ("object_record_versions", "property_versions"),
        ("object_links", "properties"),
        ("object_edits", "patch"),
        ("object_edits", "previous_values"),
        ("object_edits", "revert_payload"),
        ("object_conflicts", "source_value"),
        ("object_conflicts", "edit_value"),
        ("object_sets", "definition"),
    }
)
_EXPECTED_INDEXES = frozenset(
    {
        "ix_object_records_serving_lookup",
        "ix_object_records_type_version",
        "ix_object_records_change_sequence",
        "ix_object_record_versions_change",
        "ix_object_links_from_active",
        "ix_object_links_to_active",
        "ix_object_edits_timeline",
        "ix_object_conflicts_open",
        "ix_object_records_properties_gin",
        "ix_object_links_properties_gin",
    }
)
_GIN_INDEXES = frozenset({"ix_object_records_properties_gin", "ix_object_links_properties_gin"})


def verify(
    tenant_id: str,
    expected_role: str,
    environment: Mapping[str, str] = os.environ,
    connector: Callable[..., Any] = psycopg.connect,
) -> dict[str, object]:
    tenant_id = _validated(tenant_id, _TENANT_ID, "postgres_object_store_tenant_invalid")
    expected_role = _validated(expected_role, _IDENTIFIER, "postgres_object_store_role_invalid")
    dsn = _runtime_dsn(_required(environment, "FOUNDRY_LITE_DB_URL"))
    with connector(dsn, autocommit=True, row_factory=dict_row) as connection:
        _verify_role(connection, expected_role)
        _verify_jsonb_columns(connection)
        _verify_indexes(connection)
        _verify_rls(connection)
        tenant_rows = _verify_tenant_visibility(connection, tenant_id)
        _verify_cross_tenant_write_blocked(connection, tenant_id)
    return _receipt(expected_role, tenant_rows)


def _verify_role(connection: Any, expected_role: str) -> None:
    row = connection.execute(
        """
        SELECT current_user AS role_name, rolsuper, rolcreaterole, rolcreatedb,
               rolreplication, rolbypassrls
        FROM pg_roles WHERE rolname = current_user
        """
    ).fetchone()
    if row is None or row["role_name"] != expected_role:
        raise RuntimeError("postgres_object_store_runtime_role_mismatch")
    if any(bool(row[key]) for key in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")):
        raise RuntimeError("postgres_object_store_runtime_role_privileged")


def _verify_jsonb_columns(connection: Any) -> None:
    rows = connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND data_type = 'jsonb'
        """
    ).fetchall()
    actual = {(str(row["table_name"]), str(row["column_name"])) for row in rows}
    if not _JSONB_COLUMNS <= actual:
        raise RuntimeError("postgres_object_store_jsonb_contract_failed")


def _verify_indexes(connection: Any) -> None:
    rows = connection.execute(
        """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = current_schema() AND indexname = ANY(%s)
        """,
        (list(_EXPECTED_INDEXES),),
    ).fetchall()
    definitions = {str(row["indexname"]): str(row["indexdef"]) for row in rows}
    if set(definitions) != set(_EXPECTED_INDEXES):
        raise RuntimeError("postgres_object_store_index_contract_failed")
    if any("USING gin" not in definitions[name] or "jsonb_path_ops" not in definitions[name] for name in _GIN_INDEXES):
        raise RuntimeError("postgres_object_store_gin_contract_failed")


def _verify_rls(connection: Any) -> None:
    rows = connection.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               EXISTS (
                 SELECT 1 FROM pg_policies p
                 WHERE p.schemaname = current_schema()
                   AND p.tablename = c.relname
                   AND p.policyname = c.relname || '_tenant_isolation'
               ) AS has_tenant_policy
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
        """,
        (list(_OBJECT_STORE_TABLES),),
    ).fetchall()
    evidence = {str(row["relname"]): row for row in rows}
    if set(evidence) != set(_OBJECT_STORE_TABLES):
        raise RuntimeError("postgres_object_store_rls_table_contract_failed")
    if any(
        not row["relrowsecurity"] or not row["relforcerowsecurity"] or not row["has_tenant_policy"]
        for row in evidence.values()
    ):
        raise RuntimeError("postgres_object_store_rls_policy_contract_failed")


def _verify_tenant_visibility(connection: Any, tenant_id: str) -> int:
    without_context = _visible_count(connection)
    with connection.transaction():
        _set_tenant(connection, tenant_id)
        tenant_rows = _visible_count(connection)
    with connection.transaction():
        _set_tenant(connection, f"{tenant_id}-rls-other")
        other_rows = _visible_count(connection)
    if without_context != 0 or tenant_rows < 1 or other_rows != 0:
        raise RuntimeError("postgres_object_store_tenant_visibility_failed")
    return tenant_rows


def _verify_cross_tenant_write_blocked(connection: Any, tenant_id: str) -> None:
    try:
        with connection.transaction():
            _set_tenant(connection, tenant_id)
            connection.execute(
                "INSERT INTO object_change_counters (tenant_id, last_sequence) VALUES (%s, %s)",
                (f"{tenant_id}-rls-blocked", 0),
            )
    except psycopg.errors.InsufficientPrivilege:
        return
    raise RuntimeError("postgres_object_store_cross_tenant_write_allowed")


def _visible_count(connection: Any) -> int:
    row = connection.execute("SELECT count(*) AS visible_count FROM object_records").fetchone()
    return int(row["visible_count"])


def _set_tenant(connection: Any, tenant_id: str) -> None:
    connection.execute("SELECT set_config('foundry_lite.tenant_id', %s, true)", (tenant_id,))


def _receipt(expected_role: str, tenant_rows: int) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "databaseBackend": "postgresql",
        "runtimeRole": expected_role,
        "isSuperuser": False,
        "canBypassRls": False,
        "jsonbColumnCount": len(_JSONB_COLUMNS),
        "productionIndexCount": len(_EXPECTED_INDEXES),
        "jsonbPathOpsGinIndexCount": len(_GIN_INDEXES),
        "forcedRlsTableCount": len(_OBJECT_STORE_TABLES),
        "visibleTenantRowCount": tenant_rows,
        "noTenantRowsVisible": True,
        "otherTenantRowsVisible": False,
        "crossTenantWriteBlocked": True,
        "rawCredentialsStored": False,
    }


def _runtime_dsn(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgresql://"):
        return value
    raise ValueError("postgres_object_store_runtime_dsn_invalid")


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key, "").strip()
    if not value or len(value) > 2048:
        raise ValueError(f"postgres_object_store_environment_invalid:{key}")
    return value


def _validated(value: str, pattern: re.Pattern[str], reason: str) -> str:
    if pattern.fullmatch(value) is None:
        raise ValueError(reason)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--expected-role", default="foundry_lite_app")
    receipt = verify(**vars(parser.parse_args(argv)))
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
