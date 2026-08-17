"""expand the production object store to PostgreSQL JSONB, indexes, and RLS

Revision ID: a3e5f7b9d1c4
Revises: f2d4b6e8a0c3
Create Date: 2026-08-18 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import context, op

revision: str = "a3e5f7b9d1c4"
down_revision: str | Sequence[str] | None = "f2d4b6e8a0c3"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.get_context().dialect.name == "postgresql":
        _convert_object_documents_to_jsonb()
    _create_object_access_indexes()
    if context.get_context().dialect.name == "postgresql":
        _create_jsonb_indexes()
        _enforce_object_store_rls()


def _convert_object_documents_to_jsonb() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE object_records ALTER COLUMN properties TYPE jsonb USING properties::jsonb")
    op.execute("ALTER TABLE object_records ALTER COLUMN base_properties TYPE jsonb USING base_properties::jsonb")
    op.execute("ALTER TABLE object_records ALTER COLUMN edit_properties TYPE jsonb USING edit_properties::jsonb")
    op.execute("ALTER TABLE object_records ALTER COLUMN property_versions TYPE jsonb USING property_versions::jsonb")
    op.execute("ALTER TABLE object_record_versions ALTER COLUMN properties TYPE jsonb USING properties::jsonb")
    op.execute(
        "ALTER TABLE object_record_versions ALTER COLUMN base_properties TYPE jsonb USING base_properties::jsonb"
    )
    op.execute(
        "ALTER TABLE object_record_versions ALTER COLUMN edit_properties TYPE jsonb USING edit_properties::jsonb"
    )
    op.execute(
        "ALTER TABLE object_record_versions ALTER COLUMN property_versions TYPE jsonb USING property_versions::jsonb"
    )
    op.execute("ALTER TABLE object_links ALTER COLUMN properties TYPE jsonb USING properties::jsonb")
    op.execute("ALTER TABLE object_edits ALTER COLUMN patch TYPE jsonb USING patch::jsonb")
    op.execute("ALTER TABLE object_edits ALTER COLUMN previous_values TYPE jsonb USING previous_values::jsonb")
    op.execute("ALTER TABLE object_edits ALTER COLUMN revert_payload TYPE jsonb USING revert_payload::jsonb")
    op.execute("ALTER TABLE object_conflicts ALTER COLUMN source_value TYPE jsonb USING source_value::jsonb")
    op.execute("ALTER TABLE object_conflicts ALTER COLUMN edit_value TYPE jsonb USING edit_value::jsonb")
    op.execute("ALTER TABLE object_sets ALTER COLUMN definition TYPE jsonb USING definition::jsonb")


def _create_object_access_indexes() -> None:
    op.create_index(
        "ix_object_records_serving_lookup",
        "object_records",
        ["tenant_id", "object_type_api_name", "is_active", "deleted", "object_id"],
    )
    op.create_index(
        "ix_object_records_type_version",
        "object_records",
        ["tenant_id", "object_type_id", "index_version", "object_id"],
    )
    op.create_index(
        "ix_object_records_change_sequence",
        "object_records",
        ["tenant_id", "object_type_api_name", "is_active", "object_change_sequence"],
    )
    op.create_index(
        "ix_object_record_versions_change",
        "object_record_versions",
        ["tenant_id", "object_type_api_name", "index_version", "object_change_sequence", "object_id"],
    )
    op.create_index(
        "ix_object_links_from_active",
        "object_links",
        [
            "tenant_id",
            "link_type_api_name",
            "is_active",
            "deleted",
            "from_api_name",
            "from_object_id",
            "to_object_id",
        ],
    )
    op.create_index(
        "ix_object_links_to_active",
        "object_links",
        [
            "tenant_id",
            "link_type_api_name",
            "is_active",
            "deleted",
            "to_api_name",
            "to_object_id",
            "from_object_id",
        ],
    )
    op.create_index(
        "ix_object_edits_timeline",
        "object_edits",
        ["tenant_id", "object_type_api_name", "object_id", "created_at"],
    )
    op.create_index(
        "ix_object_conflicts_open",
        "object_conflicts",
        ["tenant_id", "object_type_id", "object_id", "status"],
    )


def _create_jsonb_indexes() -> None:
    op.create_index(
        "ix_object_records_properties_gin",
        "object_records",
        ["properties"],
        postgresql_using="gin",
        postgresql_ops={"properties": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_object_links_properties_gin",
        "object_links",
        ["properties"],
        postgresql_using="gin",
        postgresql_ops={"properties": "jsonb_path_ops"},
    )


def _enforce_object_store_rls() -> None:
    for table_name in (
        "object_records",
        "object_record_versions",
        "object_links",
        "object_edits",
        "object_conflicts",
        "object_sets",
        "object_change_counters",
        "object_index_row_hashes",
        "object_index_versions",
    ):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        _create_tenant_policy_if_missing(table_name)


def _create_tenant_policy_if_missing(table_name: str) -> None:
    op.execute(
        f"""
        DO $policy$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename = '{table_name}'
              AND policyname = '{table_name}_tenant_isolation'
          ) THEN
            EXECUTE 'CREATE POLICY {table_name}_tenant_isolation ON {table_name}
              USING (tenant_id = current_setting(''foundry_lite.tenant_id'', true))
              WITH CHECK (tenant_id = current_setting(''foundry_lite.tenant_id'', true))';
          END IF;
        END
        $policy$;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
