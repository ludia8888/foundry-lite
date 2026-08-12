"""SQLAlchemy repository adapter for osdk application repository persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import and_, delete, desc, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.osdk_application_repository import (
    OsdkApplicationClientRecord,
    OsdkApplicationClientRow,
    OsdkApplicationRecord,
    OsdkApplicationResourceRecord,
    OsdkApplicationResourceRow,
    OsdkApplicationRow,
    OsdkDeveloperConsoleIdempotencyRecord,
    OsdkDeveloperConsoleIdempotencyRow,
    OsdkLanguage,
    OsdkMcpServerRecord,
    OsdkMcpServerRow,
    OsdkMcpSessionEventRow,
    OsdkMcpSessionRecord,
    OsdkMcpSessionRow,
    OsdkReleaseArtifactDownloadTokenRecord,
    OsdkReleaseArtifactDownloadTokenRow,
    OsdkReleaseArtifactRecord,
    OsdkReleaseArtifactRow,
    OsdkSdkCompatibilityWindowRecord,
    OsdkSdkCompatibilityWindowRow,
    OsdkSdkReleaseChannelRecord,
    OsdkSdkReleaseChannelRow,
    OsdkSdkVersionRecord,
    OsdkSdkVersionRow,
)
from foundry_lite.application.ports.osdk_security_repository import (
    OsdkClientSecretVersionRecord,
    OsdkClientSecretVersionRow,
    OsdkMcpToolActivationRecord,
    OsdkMcpToolActivationRow,
)
from foundry_lite.application.ports.runtime_repository import RuntimeJsonObject
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.state_transitions import (
    OSDK_APPLICATION_CLIENT_ACTIVE,
    OSDK_APPLICATION_CLIENT_INACTIVE,
    OSDK_CLIENT_SECRET_REVOKED,
    OSDK_CLIENT_SECRET_ROTATED,
    OSDK_DOWNLOAD_TOKEN_USED,
    OSDK_MCP_SESSION_TERMINATED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyOsdkApplicationRepository:
    """SQLAlchemy Developer Console-lite app and SDK-release registry."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_application_or_get_existing(
        self, *, transaction: Any, record: OsdkApplicationRecord
    ) -> OsdkApplicationRow | None:
        inserted_id = transaction.execute(
            _insert_or_ignore(transaction, db.osdk_applications, _application_values(record))
        ).scalar_one_or_none()
        if inserted_id == record.application_id:
            return None
        return self._application_by_create_key(transaction, record.tenant_id, record.created_idempotency_key)

    def insert_client_or_get_existing(
        self, *, transaction: Any, record: OsdkApplicationClientRecord
    ) -> OsdkApplicationClientRow | None:
        inserted_id = transaction.execute(
            _insert_or_ignore(transaction, db.osdk_application_clients, _client_values(record))
        ).scalar_one_or_none()
        if inserted_id == record.client_row_id:
            return None
        return self.active_client(transaction=transaction, tenant_id=record.tenant_id, client_id=record.client_id)

    def application_by_id(self, *, transaction: Any, tenant_id: str, app_id: str) -> OsdkApplicationRow | None:
        row = (
            transaction.execute(
                select(db.osdk_applications).where(
                    and_(db.osdk_applications.c.tenant_id == tenant_id, db.osdk_applications.c.id == app_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkApplicationRow, dict(row)) if row else None

    def public_active_application_tenant(self, *, transaction: Any, app_id: str) -> str | None:
        """Return the opaque app's tenant only to the OAuth bootstrap boundary."""

        return cast(
            str | None,
            transaction.execute(
                select(db.osdk_applications.c.tenant_id).where(
                    and_(db.osdk_applications.c.id == app_id, db.osdk_applications.c.status == "active")
                )
            ).scalar_one_or_none(),
        )

    def public_active_application_client_tenant(self, *, transaction: Any, app_id: str, client_id: str) -> str | None:
        """Resolve an exact active pair before its tenant-scoped secret is checked."""

        statement = (
            select(db.osdk_application_clients.c.tenant_id)
            .join(
                db.osdk_applications,
                and_(
                    db.osdk_applications.c.id == db.osdk_application_clients.c.app_id,
                    db.osdk_applications.c.tenant_id == db.osdk_application_clients.c.tenant_id,
                ),
            )
            .where(
                and_(
                    db.osdk_applications.c.id == app_id,
                    db.osdk_applications.c.status == "active",
                    db.osdk_application_clients.c.client_id == client_id,
                    db.osdk_application_clients.c.status == "active",
                )
            )
        )
        return cast(str | None, transaction.execute(statement).scalar_one_or_none())

    def list_applications(self, *, transaction: Any, tenant_id: str) -> list[OsdkApplicationRow]:
        rows = (
            transaction.execute(
                select(db.osdk_applications)
                .where(db.osdk_applications.c.tenant_id == tenant_id)
                .order_by(desc(db.osdk_applications.c.created_at), desc(db.osdk_applications.c.id))
            )
            .mappings()
            .all()
        )
        return [cast(OsdkApplicationRow, dict(row)) for row in rows]

    def active_client(self, *, transaction: Any, tenant_id: str, client_id: str) -> OsdkApplicationClientRow | None:
        return self._active_client(transaction, tenant_id, client_id, is_for_update=False)

    def active_client_for_update(
        self, *, transaction: Any, tenant_id: str, client_id: str
    ) -> OsdkApplicationClientRow | None:
        return self._active_client(transaction, tenant_id, client_id, is_for_update=True)

    def _active_client(
        self, transaction: Any, tenant_id: str, client_id: str, *, is_for_update: bool
    ) -> OsdkApplicationClientRow | None:
        statement = select(db.osdk_application_clients).where(
            and_(
                db.osdk_application_clients.c.tenant_id == tenant_id,
                db.osdk_application_clients.c.client_id == client_id,
                db.osdk_application_clients.c.status == "active",
            )
        )
        if is_for_update:
            statement = statement.with_for_update()
        row = transaction.execute(statement).mappings().first()
        return cast(OsdkApplicationClientRow, dict(row)) if row else None

    def clients_for_application(
        self, *, transaction: Any, tenant_id: str, app_id: str
    ) -> list[OsdkApplicationClientRow]:
        rows = (
            transaction.execute(
                select(db.osdk_application_clients)
                .where(
                    and_(
                        db.osdk_application_clients.c.tenant_id == tenant_id,
                        db.osdk_application_clients.c.app_id == app_id,
                    )
                )
                .order_by(db.osdk_application_clients.c.client_id)
            )
            .mappings()
            .all()
        )
        return [cast(OsdkApplicationClientRow, dict(row)) for row in rows]

    def update_client(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        client_row_id: str,
        status: str,
        redirect_uris: tuple[str, ...],
        allowed_scopes: tuple[str, ...],
        access_token_ttl_seconds: int,
        refresh_token_ttl_seconds: int,
        updated_at: str,
    ) -> OsdkApplicationClientRow | None:
        cas_status_update(
            transaction,
            db.osdk_application_clients,
            tenant_id=tenant_id,
            row_id=client_row_id,
            transition=_client_status_transition(status),
            values={
                "redirect_uris": list(redirect_uris),
                "allowed_scopes": list(allowed_scopes),
                "access_token_ttl_seconds": access_token_ttl_seconds,
                "refresh_token_ttl_seconds": refresh_token_ttl_seconds,
                "updated_at": updated_at,
            },
        )
        row = (
            transaction.execute(
                select(db.osdk_application_clients).where(
                    and_(
                        db.osdk_application_clients.c.tenant_id == tenant_id,
                        db.osdk_application_clients.c.id == client_row_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkApplicationClientRow, dict(row)) if row else None

    def activate_client_secret(
        self, *, transaction: Any, record: OsdkClientSecretVersionRecord
    ) -> OsdkClientSecretVersionRow:
        client = self._client_by_row_id(
            transaction,
            record.tenant_id,
            record.client_row_id,
            is_for_update=True,
        )
        if client is None or client["app_id"] != record.app_id or client["client_id"] != record.client_id:
            raise RuntimeError("OSDK client secret target could not be resolved")
        current_secret_id = client.get("current_secret_id")
        if isinstance(current_secret_id, str) and current_secret_id:
            is_rotated = cas_status_update(
                transaction,
                db.osdk_client_secret_versions,
                tenant_id=record.tenant_id,
                row_id=current_secret_id,
                transition=OSDK_CLIENT_SECRET_ROTATED,
                values={"revoked_at": record.created_at},
            )
            if not is_rotated:
                raise RuntimeError("current OSDK client secret changed during rotation")
        values = _client_secret_values(record)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.osdk_client_secret_versions).values(tenant_id=record.tenant_id, **values))
        transaction.execute(
            update(db.osdk_application_clients)
            .where(
                and_(
                    db.osdk_application_clients.c.tenant_id == record.tenant_id,
                    db.osdk_application_clients.c.id == record.client_row_id,
                )
            )
            .values(current_secret_id=record.secret_id, updated_at=record.created_at)
        )
        row = self.current_client_secret(
            transaction=transaction,
            tenant_id=record.tenant_id,
            client_row_id=record.client_row_id,
        )
        if row is None:
            raise RuntimeError("activated OSDK client secret could not be read back")
        return row

    def current_client_secret(
        self, *, transaction: Any, tenant_id: str, client_row_id: str
    ) -> OsdkClientSecretVersionRow | None:
        client = self._client_by_row_id(transaction, tenant_id, client_row_id)
        secret_id = client.get("current_secret_id") if client else None
        if not isinstance(secret_id, str) or not secret_id:
            return None
        row = (
            transaction.execute(
                select(db.osdk_client_secret_versions).where(
                    and_(
                        db.osdk_client_secret_versions.c.tenant_id == tenant_id,
                        db.osdk_client_secret_versions.c.id == secret_id,
                        db.osdk_client_secret_versions.c.client_row_id == client_row_id,
                        db.osdk_client_secret_versions.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkClientSecretVersionRow, dict(row)) if row else None

    def client_secret_versions(
        self, *, transaction: Any, tenant_id: str, client_row_id: str
    ) -> list[OsdkClientSecretVersionRow]:
        rows = (
            transaction.execute(
                select(db.osdk_client_secret_versions)
                .where(
                    and_(
                        db.osdk_client_secret_versions.c.tenant_id == tenant_id,
                        db.osdk_client_secret_versions.c.client_row_id == client_row_id,
                    )
                )
                .order_by(desc(db.osdk_client_secret_versions.c.created_at), desc(db.osdk_client_secret_versions.c.id))
            )
            .mappings()
            .all()
        )
        return [cast(OsdkClientSecretVersionRow, dict(row)) for row in rows]

    def revoke_current_client_secret(
        self, *, transaction: Any, tenant_id: str, client_row_id: str, revoked_at: str
    ) -> OsdkClientSecretVersionRow | None:
        self._client_by_row_id(transaction, tenant_id, client_row_id, is_for_update=True)
        current = self.current_client_secret(
            transaction=transaction,
            tenant_id=tenant_id,
            client_row_id=client_row_id,
        )
        if current is None:
            return None
        is_revoked = cas_status_update(
            transaction,
            db.osdk_client_secret_versions,
            tenant_id=tenant_id,
            row_id=current["id"],
            transition=OSDK_CLIENT_SECRET_REVOKED,
            values={"revoked_at": revoked_at},
        )
        if not is_revoked:
            return None
        transaction.execute(
            update(db.osdk_application_clients)
            .where(
                and_(
                    db.osdk_application_clients.c.tenant_id == tenant_id,
                    db.osdk_application_clients.c.id == client_row_id,
                    db.osdk_application_clients.c.current_secret_id == current["id"],
                )
            )
            .values(current_secret_id=None, updated_at=revoked_at)
        )
        return {**current, "status": "revoked", "revoked_at": revoked_at}

    def mark_client_secret_used(self, *, transaction: Any, tenant_id: str, secret_id: str, used_at: str) -> None:
        transaction.execute(
            update(db.osdk_client_secret_versions)
            .where(
                and_(
                    db.osdk_client_secret_versions.c.tenant_id == tenant_id,
                    db.osdk_client_secret_versions.c.id == secret_id,
                    db.osdk_client_secret_versions.c.status == "active",
                )
            )
            .values(last_used_at=used_at)
        )

    def replace_resources(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        app_id: str,
        resources: tuple[OsdkApplicationResourceRecord, ...],
    ) -> None:
        transaction.execute(
            delete(db.osdk_application_resources).where(
                and_(
                    db.osdk_application_resources.c.tenant_id == tenant_id,
                    db.osdk_application_resources.c.app_id == app_id,
                )
            )
        )
        for resource in resources:
            values = _resource_values(resource)
            values.pop("tenant_id", None)
            transaction.execute(insert(db.osdk_application_resources).values(tenant_id=resource.tenant_id, **values))

    def resources_for_application(
        self, *, transaction: Any, tenant_id: str, app_id: str
    ) -> list[OsdkApplicationResourceRow]:
        rows = (
            transaction.execute(
                select(db.osdk_application_resources)
                .where(
                    and_(
                        db.osdk_application_resources.c.tenant_id == tenant_id,
                        db.osdk_application_resources.c.app_id == app_id,
                    )
                )
                .order_by(
                    db.osdk_application_resources.c.resource_type, db.osdk_application_resources.c.resource_api_name
                )
            )
            .mappings()
            .all()
        )
        return [cast(OsdkApplicationResourceRow, dict(row)) for row in rows]

    def upsert_mcp_server(self, *, transaction: Any, record: OsdkMcpServerRecord) -> OsdkMcpServerRow:
        values = _mcp_server_values(record)
        updates = {
            "status": record.status,
            "description_markdown": record.description_markdown,
            "allowed_origins": list(record.allowed_origins),
            "updated_by_user_id": record.updated_by_user_id,
            "updated_at": record.updated_at,
        }
        statement: Any
        if transaction.dialect.name == "postgresql":
            statement = (
                postgres_insert(db.osdk_mcp_servers)
                .values(**values)
                .on_conflict_do_update(index_elements=("tenant_id", "app_id"), set_=updates)
            )
        elif transaction.dialect.name == "sqlite":
            statement = (
                sqlite_insert(db.osdk_mcp_servers)
                .values(**values)
                .on_conflict_do_update(index_elements=("tenant_id", "app_id"), set_=updates)
            )
        else:
            statement = insert(db.osdk_mcp_servers).values(**values)
        transaction.execute(statement)
        row = self.mcp_server_for_application(transaction=transaction, tenant_id=record.tenant_id, app_id=record.app_id)
        if row is None:
            raise RuntimeError("upserted OSDK MCP server could not be read back")
        return row

    def mcp_server_for_application(self, *, transaction: Any, tenant_id: str, app_id: str) -> OsdkMcpServerRow | None:
        row = (
            transaction.execute(
                select(db.osdk_mcp_servers).where(
                    and_(db.osdk_mcp_servers.c.tenant_id == tenant_id, db.osdk_mcp_servers.c.app_id == app_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkMcpServerRow, dict(row)) if row else None

    def list_mcp_servers(self, *, transaction: Any, tenant_id: str) -> list[OsdkMcpServerRow]:
        rows = (
            transaction.execute(
                select(db.osdk_mcp_servers)
                .where(db.osdk_mcp_servers.c.tenant_id == tenant_id)
                .order_by(desc(db.osdk_mcp_servers.c.updated_at), db.osdk_mcp_servers.c.app_id)
            )
            .mappings()
            .all()
        )
        return [cast(OsdkMcpServerRow, dict(row)) for row in rows]

    def touch_mcp_server_activity(self, *, transaction: Any, tenant_id: str, app_id: str, observed_at: str) -> None:
        transaction.execute(
            update(db.osdk_mcp_servers)
            .where(and_(db.osdk_mcp_servers.c.tenant_id == tenant_id, db.osdk_mcp_servers.c.app_id == app_id))
            .values(last_activity_at=observed_at)
        )

    def insert_mcp_session_or_get_existing(
        self, *, transaction: Any, record: OsdkMcpSessionRecord
    ) -> OsdkMcpSessionRow | None:
        values = _mcp_session_values(record)
        statement: Any
        if transaction.dialect.name == "postgresql":
            statement = (
                postgres_insert(db.osdk_mcp_sessions).values(**values).on_conflict_do_nothing(index_elements=("id",))
            )
        elif transaction.dialect.name == "sqlite":
            statement = (
                sqlite_insert(db.osdk_mcp_sessions).values(**values).on_conflict_do_nothing(index_elements=("id",))
            )
        else:
            statement = insert(db.osdk_mcp_sessions).values(**values)
        result = transaction.execute(statement.returning(db.osdk_mcp_sessions.c.id)).scalar_one_or_none()
        if result == record.session_id:
            return None
        return self.mcp_session_by_id(transaction=transaction, tenant_id=record.tenant_id, session_id=record.session_id)

    def mcp_session_by_id(self, *, transaction: Any, tenant_id: str, session_id: str) -> OsdkMcpSessionRow | None:
        row = (
            transaction.execute(
                select(db.osdk_mcp_sessions).where(
                    and_(db.osdk_mcp_sessions.c.tenant_id == tenant_id, db.osdk_mcp_sessions.c.id == session_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkMcpSessionRow, dict(row)) if row else None

    def active_mcp_sessions_for_application(
        self, *, transaction: Any, tenant_id: str, app_id: str
    ) -> list[OsdkMcpSessionRow]:
        rows = (
            transaction.execute(
                select(db.osdk_mcp_sessions)
                .where(
                    and_(
                        db.osdk_mcp_sessions.c.tenant_id == tenant_id,
                        db.osdk_mcp_sessions.c.app_id == app_id,
                        db.osdk_mcp_sessions.c.status == "active",
                    )
                )
                .order_by(db.osdk_mcp_sessions.c.id)
            )
            .mappings()
            .all()
        )
        return [cast(OsdkMcpSessionRow, dict(row)) for row in rows]

    def claim_mcp_session_stream_lease(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        session_id: str,
        lease_id: str,
        claimed_at: str,
        lease_expires_at: str,
    ) -> OsdkMcpSessionRow | None:
        available = or_(
            db.osdk_mcp_sessions.c.stream_lease_id.is_(None),
            db.osdk_mcp_sessions.c.stream_lease_expires_at.is_(None),
            db.osdk_mcp_sessions.c.stream_lease_expires_at <= claimed_at,
        )
        row = (
            transaction.execute(
                update(db.osdk_mcp_sessions)
                .where(
                    and_(
                        db.osdk_mcp_sessions.c.tenant_id == tenant_id,
                        db.osdk_mcp_sessions.c.id == session_id,
                        db.osdk_mcp_sessions.c.status == "active",
                        available,
                    )
                )
                .values(
                    stream_lease_id=lease_id,
                    stream_lease_expires_at=lease_expires_at,
                    last_seen_at=claimed_at,
                )
                .returning(*db.osdk_mcp_sessions.c)
            )
            .mappings()
            .first()
        )
        return cast(OsdkMcpSessionRow, dict(row)) if row else None

    def release_mcp_session_stream_lease(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        session_id: str,
        lease_id: str,
        released_at: str,
    ) -> bool:
        released = transaction.execute(
            update(db.osdk_mcp_sessions)
            .where(
                and_(
                    db.osdk_mcp_sessions.c.tenant_id == tenant_id,
                    db.osdk_mcp_sessions.c.id == session_id,
                    db.osdk_mcp_sessions.c.stream_lease_id == lease_id,
                )
            )
            .values(stream_lease_id=None, stream_lease_expires_at=None, last_seen_at=released_at)
            .returning(db.osdk_mcp_sessions.c.id)
        ).scalar_one_or_none()
        return released == session_id

    def append_mcp_session_event(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        session_id: str,
        event_type: str,
        payload: RuntimeJsonObject,
        created_at: str,
    ) -> OsdkMcpSessionEventRow | None:
        sequence = transaction.execute(
            update(db.osdk_mcp_sessions)
            .where(
                and_(
                    db.osdk_mcp_sessions.c.tenant_id == tenant_id,
                    db.osdk_mcp_sessions.c.id == session_id,
                    db.osdk_mcp_sessions.c.status == "active",
                )
            )
            .values(
                last_sequence=db.osdk_mcp_sessions.c.last_sequence + 1,
                last_seen_at=created_at,
            )
            .returning(db.osdk_mcp_sessions.c.last_sequence)
        ).scalar_one_or_none()
        if sequence is None:
            return None
        values = {
            "id": f"{session_id}:{sequence}",
            "tenant_id": tenant_id,
            "session_id": session_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload_json": payload,
            "created_at": created_at,
        }
        transaction.execute(
            insert(db.osdk_mcp_session_events).values(
                tenant_id=tenant_id,
                id=values["id"],
                session_id=session_id,
                sequence=sequence,
                event_type=event_type,
                payload_json=payload,
                created_at=created_at,
            )
        )
        return cast(OsdkMcpSessionEventRow, values)

    def mcp_session_events_after(
        self, *, transaction: Any, tenant_id: str, session_id: str, after_sequence: int
    ) -> list[OsdkMcpSessionEventRow]:
        rows = (
            transaction.execute(
                select(db.osdk_mcp_session_events)
                .where(
                    and_(
                        db.osdk_mcp_session_events.c.tenant_id == tenant_id,
                        db.osdk_mcp_session_events.c.session_id == session_id,
                        db.osdk_mcp_session_events.c.sequence > after_sequence,
                    )
                )
                .order_by(db.osdk_mcp_session_events.c.sequence)
            )
            .mappings()
            .all()
        )
        return [cast(OsdkMcpSessionEventRow, dict(row)) for row in rows]

    def terminate_mcp_session(
        self, *, transaction: Any, tenant_id: str, session_id: str, terminated_at: str
    ) -> OsdkMcpSessionRow | None:
        is_terminated = cas_status_update(
            transaction,
            db.osdk_mcp_sessions,
            tenant_id=tenant_id,
            row_id=session_id,
            transition=OSDK_MCP_SESSION_TERMINATED,
            values={
                "terminated_at": terminated_at,
                "last_seen_at": terminated_at,
                "stream_lease_id": None,
                "stream_lease_expires_at": None,
            },
        )
        if not is_terminated:
            return None
        return self.mcp_session_by_id(transaction=transaction, tenant_id=tenant_id, session_id=session_id)

    def activate_mcp_tool(self, *, transaction: Any, record: OsdkMcpToolActivationRecord) -> bool:
        values = _mcp_tool_activation_values(record)
        conflict = ("tenant_id", "app_id", "session_id", "client_id", "actor_user_id", "tool_id")
        statement: Any
        if transaction.dialect.name == "postgresql":
            statement = (
                postgres_insert(db.osdk_mcp_tool_activations)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict)
            )
        elif transaction.dialect.name == "sqlite":
            statement = (
                sqlite_insert(db.osdk_mcp_tool_activations)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict)
            )
        else:
            statement = insert(db.osdk_mcp_tool_activations).values(**values)
        return (
            transaction.execute(statement.returning(db.osdk_mcp_tool_activations.c.id)).scalar_one_or_none() is not None
        )

    def mcp_tool_activations(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        app_id: str,
        session_id: str,
        client_id: str,
        actor_user_id: str,
    ) -> list[OsdkMcpToolActivationRow]:
        rows = (
            transaction.execute(
                select(db.osdk_mcp_tool_activations)
                .where(
                    and_(
                        db.osdk_mcp_tool_activations.c.tenant_id == tenant_id,
                        db.osdk_mcp_tool_activations.c.app_id == app_id,
                        db.osdk_mcp_tool_activations.c.session_id == session_id,
                        db.osdk_mcp_tool_activations.c.client_id == client_id,
                        db.osdk_mcp_tool_activations.c.actor_user_id == actor_user_id,
                    )
                )
                .order_by(db.osdk_mcp_tool_activations.c.tool_id)
            )
            .mappings()
            .all()
        )
        return [cast(OsdkMcpToolActivationRow, dict(row)) for row in rows]

    def insert_sdk_version_or_get_existing(
        self, *, transaction: Any, record: OsdkSdkVersionRecord
    ) -> OsdkSdkVersionRow | None:
        inserted_id = transaction.execute(
            _insert_or_ignore(transaction, db.osdk_sdk_versions, _sdk_version_values(record))
        ).scalar_one_or_none()
        if inserted_id == record.sdk_version_id:
            return None
        return self._sdk_version_by_create_key(
            transaction,
            record.tenant_id,
            record.app_id,
            record.created_idempotency_key,
        )

    def sdk_version_by_id(self, *, transaction: Any, tenant_id: str, version_id: str) -> OsdkSdkVersionRow | None:
        row = (
            transaction.execute(
                select(db.osdk_sdk_versions).where(
                    and_(db.osdk_sdk_versions.c.tenant_id == tenant_id, db.osdk_sdk_versions.c.id == version_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkSdkVersionRow, dict(row)) if row else None

    def sdk_versions_for_application(self, *, transaction: Any, tenant_id: str, app_id: str) -> list[OsdkSdkVersionRow]:
        rows = (
            transaction.execute(
                select(db.osdk_sdk_versions)
                .where(and_(db.osdk_sdk_versions.c.tenant_id == tenant_id, db.osdk_sdk_versions.c.app_id == app_id))
                .order_by(desc(db.osdk_sdk_versions.c.created_at), desc(db.osdk_sdk_versions.c.id))
            )
            .mappings()
            .all()
        )
        return [cast(OsdkSdkVersionRow, dict(row)) for row in rows]

    def insert_release_artifact(self, *, transaction: Any, record: OsdkReleaseArtifactRecord) -> OsdkReleaseArtifactRow:
        values = _artifact_values(record)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.osdk_release_artifacts).values(tenant_id=record.tenant_id, **values))
        row = self.release_artifact(
            transaction=transaction,
            tenant_id=record.tenant_id,
            sdk_version_id=record.sdk_version_id,
            artifact_kind=record.artifact_kind,
        )
        if row is None:
            raise RuntimeError("inserted OSDK release artifact could not be read back")
        return row

    def release_artifact(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        sdk_version_id: str,
        artifact_kind: str,
    ) -> OsdkReleaseArtifactRow | None:
        row = (
            transaction.execute(
                select(db.osdk_release_artifacts).where(
                    and_(
                        db.osdk_release_artifacts.c.tenant_id == tenant_id,
                        db.osdk_release_artifacts.c.sdk_version_id == sdk_version_id,
                        db.osdk_release_artifacts.c.artifact_kind == artifact_kind,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkReleaseArtifactRow, dict(row)) if row else None

    def release_artifact_by_id(
        self, *, transaction: Any, tenant_id: str, artifact_id: str
    ) -> OsdkReleaseArtifactRow | None:
        row = (
            transaction.execute(
                select(db.osdk_release_artifacts).where(
                    and_(
                        db.osdk_release_artifacts.c.tenant_id == tenant_id,
                        db.osdk_release_artifacts.c.id == artifact_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkReleaseArtifactRow, dict(row)) if row else None

    def upsert_release_channel(
        self, *, transaction: Any, record: OsdkSdkReleaseChannelRecord
    ) -> OsdkSdkReleaseChannelRow:
        values = _release_channel_values(record)
        statement: Any
        if transaction.dialect.name == "postgresql":
            statement = (
                postgres_insert(db.osdk_sdk_release_channels)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=("tenant_id", "app_id", "language", "channel"),
                    set_={
                        "sdk_version_id": record.sdk_version_id,
                        "promoted_at": record.promoted_at,
                        "promoted_by_user_id": record.promoted_by_user_id,
                    },
                )
            )
        elif transaction.dialect.name == "sqlite":
            statement = (
                sqlite_insert(db.osdk_sdk_release_channels)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=("tenant_id", "app_id", "language", "channel"),
                    set_={
                        "sdk_version_id": record.sdk_version_id,
                        "promoted_at": record.promoted_at,
                        "promoted_by_user_id": record.promoted_by_user_id,
                    },
                )
            )
        else:
            statement = insert(db.osdk_sdk_release_channels).values(**values)
        transaction.execute(statement)
        return self._release_channel(transaction, record.tenant_id, record.app_id, record.language, record.channel)

    def release_channels_for_application(
        self, *, transaction: Any, tenant_id: str, app_id: str, language: OsdkLanguage | None = None
    ) -> list[OsdkSdkReleaseChannelRow]:
        predicate = and_(
            db.osdk_sdk_release_channels.c.tenant_id == tenant_id,
            db.osdk_sdk_release_channels.c.app_id == app_id,
        )
        if language is not None:
            predicate = and_(predicate, db.osdk_sdk_release_channels.c.language == language)
        rows = (
            transaction.execute(
                select(db.osdk_sdk_release_channels).where(predicate).order_by(db.osdk_sdk_release_channels.c.channel)
            )
            .mappings()
            .all()
        )
        return [cast(OsdkSdkReleaseChannelRow, dict(row)) for row in rows]

    def insert_compatibility_window(
        self, *, transaction: Any, record: OsdkSdkCompatibilityWindowRecord
    ) -> OsdkSdkCompatibilityWindowRow:
        values = _compatibility_window_values(record)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.osdk_sdk_compatibility_windows).values(tenant_id=record.tenant_id, **values))
        row = (
            transaction.execute(
                select(db.osdk_sdk_compatibility_windows).where(
                    and_(
                        db.osdk_sdk_compatibility_windows.c.tenant_id == record.tenant_id,
                        db.osdk_sdk_compatibility_windows.c.id == record.window_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise RuntimeError("inserted OSDK compatibility window could not be read back")
        return cast(OsdkSdkCompatibilityWindowRow, dict(row))

    def compatibility_windows_for_application(
        self, *, transaction: Any, tenant_id: str, app_id: str
    ) -> list[OsdkSdkCompatibilityWindowRow]:
        rows = (
            transaction.execute(
                select(db.osdk_sdk_compatibility_windows)
                .where(
                    and_(
                        db.osdk_sdk_compatibility_windows.c.tenant_id == tenant_id,
                        db.osdk_sdk_compatibility_windows.c.app_id == app_id,
                    )
                )
                .order_by(desc(db.osdk_sdk_compatibility_windows.c.created_at))
            )
            .mappings()
            .all()
        )
        return [cast(OsdkSdkCompatibilityWindowRow, dict(row)) for row in rows]

    def insert_artifact_download_token(
        self, *, transaction: Any, record: OsdkReleaseArtifactDownloadTokenRecord
    ) -> OsdkReleaseArtifactDownloadTokenRow:
        values = _artifact_download_token_values(record)
        values.pop("tenant_id", None)
        transaction.execute(
            insert(db.osdk_release_artifact_download_tokens).values(tenant_id=record.tenant_id, **values)
        )
        row = self.artifact_download_token_by_hash(
            transaction=transaction, tenant_id=record.tenant_id, token_hash=record.token_hash
        )
        if row is None:
            raise RuntimeError("inserted OSDK artifact download token could not be read back")
        return row

    def insert_artifact_download_token_or_get_existing(
        self, *, transaction: Any, record: OsdkReleaseArtifactDownloadTokenRecord
    ) -> OsdkReleaseArtifactDownloadTokenRow:
        transaction.execute(
            _insert_or_ignore(
                transaction,
                db.osdk_release_artifact_download_tokens,
                _artifact_download_token_values(record),
            )
        )
        row = self.artifact_download_token_by_hash(
            transaction=transaction,
            tenant_id=record.tenant_id,
            token_hash=record.token_hash,
        )
        if row is None:
            raise RuntimeError("OSDK artifact download token could not be read back")
        return row

    def artifact_download_token_by_hash(
        self, *, transaction: Any, tenant_id: str, token_hash: str
    ) -> OsdkReleaseArtifactDownloadTokenRow | None:
        row = (
            transaction.execute(
                select(db.osdk_release_artifact_download_tokens).where(
                    and_(
                        db.osdk_release_artifact_download_tokens.c.tenant_id == tenant_id,
                        db.osdk_release_artifact_download_tokens.c.token_hash == token_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkReleaseArtifactDownloadTokenRow, dict(row)) if row else None

    def mark_artifact_download_token_used(
        self, *, transaction: Any, tenant_id: str, token_id: str, used_at: str
    ) -> None:
        cas_status_update(
            transaction,
            db.osdk_release_artifact_download_tokens,
            tenant_id=tenant_id,
            row_id=token_id,
            transition=OSDK_DOWNLOAD_TOKEN_USED,
            values={"used_at": used_at},
        )

    def developer_console_idempotency_record(
        self, *, transaction: Any, tenant_id: str, operation: str, idempotency_key: str
    ) -> OsdkDeveloperConsoleIdempotencyRow | None:
        row = (
            transaction.execute(
                select(db.osdk_developer_console_idempotency_records).where(
                    and_(
                        db.osdk_developer_console_idempotency_records.c.tenant_id == tenant_id,
                        db.osdk_developer_console_idempotency_records.c.operation == operation,
                        db.osdk_developer_console_idempotency_records.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkDeveloperConsoleIdempotencyRow, dict(row)) if row else None

    def insert_developer_console_idempotency_record(
        self, *, transaction: Any, record: OsdkDeveloperConsoleIdempotencyRecord
    ) -> OsdkDeveloperConsoleIdempotencyRow:
        transaction.execute(
            _insert_or_ignore(
                transaction,
                db.osdk_developer_console_idempotency_records,
                _idempotency_values(record),
            )
        )
        row = self.developer_console_idempotency_record(
            transaction=transaction,
            tenant_id=record.tenant_id,
            operation=record.operation,
            idempotency_key=record.idempotency_key,
        )
        if row is None:
            raise RuntimeError("inserted OSDK developer console idempotency record could not be read back")
        return row

    def _client_by_row_id(
        self,
        transaction: Any,
        tenant_id: str,
        client_row_id: str,
        *,
        is_for_update: bool = False,
    ) -> OsdkApplicationClientRow | None:
        statement = select(db.osdk_application_clients).where(
            and_(
                db.osdk_application_clients.c.tenant_id == tenant_id,
                db.osdk_application_clients.c.id == client_row_id,
            )
        )
        if is_for_update:
            statement = statement.with_for_update()
        row = transaction.execute(statement).mappings().first()
        return cast(OsdkApplicationClientRow, dict(row)) if row else None

    def _application_by_create_key(
        self, transaction: Any, tenant_id: str, idempotency_key: str
    ) -> OsdkApplicationRow | None:
        row = (
            transaction.execute(
                select(db.osdk_applications).where(
                    and_(
                        db.osdk_applications.c.tenant_id == tenant_id,
                        db.osdk_applications.c.created_idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkApplicationRow, dict(row)) if row else None

    def _sdk_version_by_create_key(
        self, transaction: Any, tenant_id: str, app_id: str, idempotency_key: str
    ) -> OsdkSdkVersionRow | None:
        row = (
            transaction.execute(
                select(db.osdk_sdk_versions).where(
                    and_(
                        db.osdk_sdk_versions.c.tenant_id == tenant_id,
                        db.osdk_sdk_versions.c.app_id == app_id,
                        db.osdk_sdk_versions.c.created_idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OsdkSdkVersionRow, dict(row)) if row else None

    def _release_channel(
        self, transaction: Any, tenant_id: str, app_id: str, language: str, channel: str
    ) -> OsdkSdkReleaseChannelRow:
        row = (
            transaction.execute(
                select(db.osdk_sdk_release_channels).where(
                    and_(
                        db.osdk_sdk_release_channels.c.tenant_id == tenant_id,
                        db.osdk_sdk_release_channels.c.app_id == app_id,
                        db.osdk_sdk_release_channels.c.language == language,
                        db.osdk_sdk_release_channels.c.channel == channel,
                    )
                )
            )
            .mappings()
            .one()
        )
        return cast(OsdkSdkReleaseChannelRow, dict(row))


def _insert_or_ignore(transaction: Any, table: Any, values: dict[str, object]) -> Any:
    conflict_columns = _conflict_columns(table.name)
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
            .returning(table.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
            .returning(table.c.id)
        )
    return insert(table).values(**values).returning(table.c.id)


def _conflict_columns(table_name: str) -> Sequence[str]:
    if table_name == "osdk_applications":
        return ("tenant_id", "created_idempotency_key")
    if table_name == "osdk_application_clients":
        return ("tenant_id", "client_id")
    if table_name == "osdk_release_artifact_download_tokens":
        return ("tenant_id", "token_hash")
    if table_name == "osdk_developer_console_idempotency_records":
        return ("tenant_id", "operation", "idempotency_key")
    return ("tenant_id", "app_id", "created_idempotency_key")


def _application_values(record: OsdkApplicationRecord) -> dict[str, object]:
    return {
        "id": record.application_id,
        "tenant_id": record.tenant_id,
        "app_api_name": record.app_api_name,
        "display_name": record.display_name,
        "status": record.status,
        "owner_user_id": record.owner_user_id,
        "created_idempotency_key": record.created_idempotency_key,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _client_values(record: OsdkApplicationClientRecord) -> dict[str, object]:
    return {
        "id": record.client_row_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "client_id": record.client_id,
        "status": record.status,
        "redirect_uris": list(record.redirect_uris),
        "allowed_scopes": list(record.allowed_scopes),
        "access_token_ttl_seconds": record.access_token_ttl_seconds,
        "refresh_token_ttl_seconds": record.refresh_token_ttl_seconds,
        "created_at": record.created_at,
        "updated_at": record.updated_at or record.created_at,
    }


def _client_secret_values(record: OsdkClientSecretVersionRecord) -> dict[str, object]:
    return {
        "id": record.secret_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "client_row_id": record.client_row_id,
        "client_id": record.client_id,
        "vault_secret_name": record.vault_secret_name,
        "vault_secret_version": record.vault_secret_version,
        "status": record.status,
        "created_by_user_id": record.created_by_user_id,
        "rotation_reason": record.rotation_reason,
        "created_at": record.created_at,
        "revoked_at": None,
        "last_used_at": None,
    }


def _resource_values(record: OsdkApplicationResourceRecord) -> dict[str, object]:
    return {
        "id": record.resource_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "resource_type": record.resource_type,
        "resource_api_name": record.resource_api_name,
        "scopes": list(record.scopes),
        "created_at": record.created_at,
    }


def _mcp_server_values(record: OsdkMcpServerRecord) -> dict[str, object]:
    return {
        "id": record.server_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "status": record.status,
        "description_markdown": record.description_markdown,
        "allowed_origins": list(record.allowed_origins),
        "last_activity_at": record.last_activity_at,
        "updated_by_user_id": record.updated_by_user_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _mcp_session_values(record: OsdkMcpSessionRecord) -> dict[str, object]:
    return {
        "id": record.session_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "client_id": record.client_id,
        "actor_user_id": record.actor_user_id,
        "status": record.status,
        "last_sequence": 0,
        "created_at": record.created_at,
        "last_seen_at": record.last_seen_at,
        "terminated_at": None,
        "stream_lease_id": None,
        "stream_lease_expires_at": None,
    }


def _mcp_tool_activation_values(record: OsdkMcpToolActivationRecord) -> dict[str, object]:
    return {
        "id": record.activation_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "session_id": record.session_id,
        "client_id": record.client_id,
        "actor_user_id": record.actor_user_id,
        "tool_id": record.tool_id,
        "query_hash": record.query_hash,
        "activated_at": record.activated_at,
    }


def _sdk_version_values(record: OsdkSdkVersionRecord) -> dict[str, object]:
    return {
        "id": record.sdk_version_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "language": record.language,
        "package_name": record.package_name,
        "version": record.version,
        "generator_name": record.generator_name,
        "generator_version": record.generator_version,
        "ontology_fingerprint": record.ontology_fingerprint,
        "resource_scope_snapshot": record.resource_scope_snapshot,
        "compatibility_status": record.compatibility_status,
        "release_status": record.release_status,
        "artifact_hash": record.artifact_hash,
        "artifact_uri": record.artifact_uri,
        "created_idempotency_key": record.created_idempotency_key,
        "created_at": record.created_at,
        "released_at": record.released_at,
    }


def _artifact_values(record: OsdkReleaseArtifactRecord) -> dict[str, object]:
    return {
        "id": record.artifact_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "sdk_version_id": record.sdk_version_id,
        "artifact_kind": record.artifact_kind,
        "content_hash": record.content_hash,
        "storage_uri": record.storage_uri,
        "metadata_json": record.metadata_json,
        "created_at": record.created_at,
    }


def _release_channel_values(record: OsdkSdkReleaseChannelRecord) -> dict[str, object]:
    return {
        "id": record.channel_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "language": record.language,
        "channel": record.channel,
        "sdk_version_id": record.sdk_version_id,
        "promoted_at": record.promoted_at,
        "promoted_by_user_id": record.promoted_by_user_id,
    }


def _compatibility_window_values(record: OsdkSdkCompatibilityWindowRecord) -> dict[str, object]:
    return {
        "id": record.window_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "language": record.language,
        "from_sdk_version_id": record.from_sdk_version_id,
        "to_sdk_version_id": record.to_sdk_version_id,
        "status": record.status,
        "supported_until": record.supported_until,
        "created_at": record.created_at,
    }


def _artifact_download_token_values(record: OsdkReleaseArtifactDownloadTokenRecord) -> dict[str, object]:
    return {
        "id": record.token_id,
        "tenant_id": record.tenant_id,
        "artifact_id": record.artifact_id,
        "token_hash": record.token_hash,
        "status": record.status,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "used_at": None,
    }


def _client_status_transition(status: str) -> StatusTransition:
    if status == "active":
        return OSDK_APPLICATION_CLIENT_ACTIVE
    return OSDK_APPLICATION_CLIENT_INACTIVE


def _idempotency_values(record: OsdkDeveloperConsoleIdempotencyRecord) -> dict[str, object]:
    return {
        "id": record.record_id,
        "tenant_id": record.tenant_id,
        "operation": record.operation,
        "idempotency_key": record.idempotency_key,
        "request_hash": record.request_hash,
        "response_json": record.response_json,
        "status": record.status,
        "created_at": record.created_at,
    }
