"""add RLS-safe public OSDK OAuth bootstrap lookups

Revision ID: c4f6a8b0d2e5
Revises: a3e5f7b9d1c4
Create Date: 2026-08-31 18:30:00.000000
"""

from collections.abc import Sequence

from alembic import context, op

revision: str = "c4f6a8b0d2e5"
down_revision: str | Sequence[str] | None = "a3e5f7b9d1c4"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.get_context().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.foundry_lite_active_osdk_application_tenant(requested_app_id text)
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
            SELECT application.tenant_id
            FROM public.osdk_applications AS application
            WHERE application.id = requested_app_id AND application.status = 'active'
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.foundry_lite_active_osdk_application_client_tenant(
            requested_app_id text,
            requested_client_id text
        )
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
            SELECT application.tenant_id
            FROM public.osdk_applications AS application
            JOIN public.osdk_application_clients AS client
              ON client.app_id = application.id
             AND client.tenant_id = application.tenant_id
            WHERE application.id = requested_app_id
              AND application.status = 'active'
              AND client.client_id = requested_client_id
              AND client.status = 'active'
        $function$
        """
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
