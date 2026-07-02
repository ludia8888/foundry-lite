"""Database creation, Alembic stamping, and tenant row-level security policies."""

from __future__ import annotations

from sqlalchemy import Table, text
from sqlalchemy.engine import Connection, Engine

from foundry_lite.infrastructure.schema.base import POSTGRES_TENANT_SETTING, metadata
from foundry_lite.infrastructure.schema.datasets import dataset_schemas, datasets


def tenant_rls_tables() -> tuple[Table, ...]:
    return tuple(table for table in metadata.sorted_tables if "tenant_id" in table.c)


def create_database(engine: Engine) -> None:
    metadata.create_all(engine)
    apply_postgres_rls(engine)
    _stamp_migration_head_if_unversioned(engine)


def _stamp_migration_head_if_unversioned(engine: Engine) -> None:
    """Bind a create_all-bootstrapped database to the Alembic migration lineage.

    create_all builds the current (head) schema but records no alembic_version row,
    so a later `alembic upgrade head` restarts from base and fails on already-existing
    tables. Stamping head once makes bootstrap and the migration chain converge: fresh
    databases become migration-ready and run_migrations treats them as already at head
    (a no-op) instead of leaving two divergent lineages. Databases already under Alembic
    control are left untouched so their pending migrations still run.
    """
    from pathlib import Path

    from sqlalchemy import inspect as sqlalchemy_inspect

    with engine.connect() as connection:
        if sqlalchemy_inspect(connection).has_table("alembic_version"):
            return
        from alembic import command
        from alembic.config import Config

        # schema/rls.py 기준: schema -> infrastructure -> foundry_lite -> libs -> repo root
        repo_root = Path(__file__).resolve().parents[4]
        config = Config(str(repo_root / "alembic.ini"))
        config.set_main_option("script_location", str(repo_root / "migrations"))
        config.attributes["connection"] = connection
        command.stamp(config, "head")
        if connection.in_transaction():
            connection.commit()


def apply_postgres_rls(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in tenant_rls_tables():
            _apply_tenant_rls_policy(conn, table)
        _apply_dataset_schema_rls_policy(conn)


def set_postgres_tenant_context(conn: Connection, tenant_id: str, *, is_local: bool = True) -> None:
    if conn.dialect.name != "postgresql":
        return
    conn.execute(
        text(f"SELECT set_config('{POSTGRES_TENANT_SETTING}', :tenant_id, :local)"),
        {"tenant_id": tenant_id, "local": is_local},
    )


def _apply_tenant_rls_policy(conn: Connection, table: Table) -> None:
    table_name = _table_identifier(conn, table)
    policy_name = _identifier(conn, f"{table.name}_tenant_isolation")
    condition = f"tenant_id = current_setting('{POSTGRES_TENANT_SETTING}', true)"
    conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    conn.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    conn.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
    conn.execute(text(f"CREATE POLICY {policy_name} ON {table_name} USING ({condition}) WITH CHECK ({condition})"))


def _apply_dataset_schema_rls_policy(conn: Connection) -> None:
    table_name = _table_identifier(conn, dataset_schemas)
    datasets_name = _table_identifier(conn, datasets)
    policy_name = _identifier(conn, "dataset_schemas_tenant_isolation")
    # Policy DDL must interpolate identifiers; both names come from static
    # SQLAlchemy metadata and are quoted by the active dialect preparer.
    condition = (
        f"EXISTS (SELECT 1 FROM {datasets_name} AS d "  # nosec B608
        f"WHERE d.id = {table_name}.dataset_id "
        f"AND d.tenant_id = current_setting('{POSTGRES_TENANT_SETTING}', true))"
    )
    conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    conn.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    conn.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
    conn.execute(text(f"CREATE POLICY {policy_name} ON {table_name} USING ({condition}) WITH CHECK ({condition})"))


def _table_identifier(conn: Connection, table: Table) -> str:
    return conn.dialect.identifier_preparer.format_table(table)


def _identifier(conn: Connection, name: str) -> str:
    return conn.dialect.identifier_preparer.quote(name)
