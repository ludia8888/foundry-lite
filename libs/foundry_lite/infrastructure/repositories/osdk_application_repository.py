"""SQLAlchemy repository adapter for osdk application repository persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import and_, delete, desc, insert, select
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
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.state_transitions import (
    OSDK_APPLICATION_CLIENT_ACTIVE,
    OSDK_APPLICATION_CLIENT_INACTIVE,
    OSDK_DOWNLOAD_TOKEN_USED,
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
        row = (
            transaction.execute(
                select(db.osdk_application_clients).where(
                    and_(
                        db.osdk_application_clients.c.tenant_id == tenant_id,
                        db.osdk_application_clients.c.client_id == client_id,
                        db.osdk_application_clients.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
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
