"""Application service helpers for source onboarding service workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import BinaryIO

from foundry_lite.application.ports import (
    ConnectorRegistryRepository,
    DatasetTransactionRepository,
    SourceConnectionAlreadyExistsError,
    SourceConnectionRecord,
    SourceConnectionRow,
    SourceRegistryRepository,
)
from foundry_lite.application.ports.resource_catalog_repository import ResourceCatalogRepository
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadService
from foundry_lite.application.services.resource_catalog_auto_registration import upsert_source_resource
from foundry_lite.application.services.source_onboarding_config import (
    SourceUpload,
    StagedMediaSourceUpload,
    StagedSourceUpload,
    cleanup_media_upload,
    cleanup_uploads,
    copy_media_upload,
    copy_uploads,
    require_idempotency_key,
    source_record,
    upload_summary,
)
from foundry_lite.application.services.source_onboarding_helpers import (
    DatasetRegistryBoundary,
    SourceRuntimeBoundary,
    commit_batch_uploads,
    commit_list,
    commit_staged_media,
    debezium_record,
    record_batch_commits,
    record_media_commit,
    record_optional_commit,
    record_single_commit,
    require_kind,
    require_same_config,
    require_source_fingerprint,
    require_write,
    rest_connection,
    rest_connections,
    rest_source_view_for_connection,
    source_row,
    source_row_in_tx,
    stream_config,
    summary_text,
    webhook_record,
)
from foundry_lite.application.services.source_onboarding_views import (
    source_result,
    source_view,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class SourceOnboardingService(CoreService):
    """Product-facing Source onboarding loop for browser/SDK workflows."""

    required_dependencies = (
        "engine",
        "policy",
        "root",
        "connector_registry_repository",
        "source_registry_repository",
        "dataset_transaction_repository",
        "secret_provider",
        "resource_catalog_repository",
    )
    required_collaborators = (
        "dataset_ingest_service",
        "dataset_registry_service",
        "media_transaction_service",
        "media_upload_service",
        "runtime_service",
    )
    connector_registry_repository: ConnectorRegistryRepository
    source_registry_repository: SourceRegistryRepository
    dataset_transaction_repository: DatasetTransactionRepository
    resource_catalog_repository: ResourceCatalogRepository
    dataset_ingest_service: DatasetIngestService
    dataset_registry_service: DatasetRegistryBoundary
    media_transaction_service: MediaTransactionService
    media_upload_service: MediaUploadService
    runtime_service: SourceRuntimeBoundary

    def list_sources(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        rows = self.source_registry_repository.list_sources(tenant_id=ctx.tenant_id)
        registry_views = [source_view(row) for row in rows]
        rest_views = [
            rest_source_view_for_connection(self.engine, self.connector_registry_repository, ctx, row)
            for row in rest_connections(self.connector_registry_repository, ctx)
        ]
        names = {str(view["sourceName"]) for view in registry_views}
        return [*registry_views, *[view for view in rest_views if str(view["sourceName"]) not in names]]

    def get_source(self, source_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        with self.engine.begin() as conn:
            row = self.source_registry_repository.source_by_name(
                transaction=conn, tenant_id=ctx.tenant_id, source_name=source_name
            )
        if row is not None:
            return source_view(row)
        return rest_source_view_for_connection(
            self.engine,
            self.connector_registry_repository,
            ctx,
            rest_connection(self.engine, self.connector_registry_repository, ctx, source_name),
        )

    def upload_csv(
        self,
        *,
        source_name: str,
        display_name: str,
        dataset_ref: str,
        file_name: str,
        source: BinaryIO,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        primary_key: Sequence[str] = (),
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "upload_csv", source_name)
        require_idempotency_key(idempotency_key)
        staged = copy_uploads(self.root, source_name, [SourceUpload(file_name, dataset_ref, source)])
        try:
            row, is_replayed = self._create_upload_source(
                ctx, source_name, display_name, staged, sync_name, primary_key
            )
            if is_replayed and row["last_commit_ref"]:
                return source_result(row, commit=row["last_commit_ref"], is_replayed=True)
            self.dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx, primary_key=list(primary_key))
            commit = self.dataset_ingest_service.upload_csv(dataset_ref, staged[0].path, ctx=ctx, sync_name=sync_name)
            return record_single_commit(
                self.engine,
                self.source_registry_repository,
                self.dataset_transaction_repository,
                ctx,
                row["source_name"],
                commit,
                is_replayed,
            )
        finally:
            cleanup_uploads(staged)

    def upload_batch_files(
        self,
        *,
        source_name: str,
        display_name: str,
        uploads: Sequence[SourceUpload],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "upload_batch_files", source_name)
        require_idempotency_key(idempotency_key)
        staged = copy_uploads(self.root, source_name, uploads)
        try:
            row, is_replayed = self._create_batch_source(ctx, source_name, display_name, staged, sync_name)
            if is_replayed and row["last_commit_ref"]:
                return source_result(row, commits=commit_list(row["last_commit_ref"]), is_replayed=True)
            commits = commit_batch_uploads(
                self.engine,
                self.dataset_transaction_repository,
                self.dataset_registry_service,
                self.dataset_ingest_service,
                ctx,
                staged,
                sync_name,
            )
            return record_batch_commits(
                self.engine,
                self.source_registry_repository,
                ctx,
                row["source_name"],
                commits,
                is_replayed,
            )
        finally:
            cleanup_uploads(staged)

    def create_webhook_listener(
        self,
        *,
        source_name: str,
        display_name: str,
        dataset_ref: str,
        connector_name: str,
        resource_name: str,
        signing_secret_ref: str,
        inbound_url: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "create_webhook_listener", source_name)
        require_idempotency_key(idempotency_key)
        self.dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx)
        row, is_replayed = self._create_source(
            ctx,
            webhook_record(
                ctx,
                source_name,
                display_name,
                dataset_ref,
                connector_name,
                resource_name,
                signing_secret_ref,
                inbound_url,
            ),
        )
        return source_result(row, is_replayed=is_replayed)

    def ingest_webhook_listener_event(
        self,
        source_name: str,
        *,
        payload: Mapping[str, object],
        raw_body: bytes,
        signature: str,
        signature_timestamp: str,
        event_id: str | None,
        require_service_principal_signature: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        row = source_row(self.engine, self.source_registry_repository, ctx, source_name)
        require_kind(row, "webhook_listener")
        summary = row["config_summary"]
        commit = self.dataset_ingest_service.ingest_webhook_event(
            summary_text(summary, "datasetRef"),
            connector_name=summary_text(summary, "connectorName"),
            resource_name=summary_text(summary, "resourceName"),
            payload=payload,
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            secret=self.secret_provider.get_secret(summary_text(summary, "signingSecretRef")).value,
            event_id=event_id,
            require_service_principal_signature=require_service_principal_signature,
            ctx=ctx,
        )
        return record_single_commit(
            self.engine,
            self.source_registry_repository,
            self.dataset_transaction_repository,
            ctx,
            row["source_name"],
            commit,
            False,
        )

    def create_debezium_source(
        self,
        *,
        source_name: str,
        display_name: str,
        dataset_ref: str,
        stream_name: str,
        topic: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        consumer_group: str = "foundry-lite-cdc",
        secret_refs: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "create_debezium_source", source_name)
        require_idempotency_key(idempotency_key)
        self.dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx)
        row, is_replayed = self._create_source(
            ctx,
            debezium_record(
                ctx, source_name, display_name, dataset_ref, stream_name, topic, consumer_group, secret_refs or {}
            ),
        )
        return source_result(row, is_replayed=is_replayed)

    def start_debezium_sync(
        self,
        source_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        expected_config_fingerprint: str | None = None,
        after_offset: int | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "start_debezium_sync", source_name)
        require_idempotency_key(idempotency_key)
        row = source_row(self.engine, self.source_registry_repository, ctx, source_name)
        require_source_fingerprint(row, expected_config_fingerprint)
        commit = self.dataset_ingest_service.archive_stream_events(
            summary_text(row["config_summary"], "datasetRef"),
            stream=stream_config(row["config_summary"], limit),
            ctx=ctx,
            after_offset=after_offset,
            sync_name=f"cdc:{source_name}",
        )
        return record_optional_commit(
            self.engine,
            self.source_registry_repository,
            self.dataset_transaction_repository,
            ctx,
            row["source_name"],
            commit,
        )

    def upload_media(
        self,
        *,
        source_name: str,
        display_name: str,
        media_set_id: str,
        logical_path: str,
        file_name: str,
        source: BinaryIO,
        supplied_mime_type: str,
        schema_type: str,
        format: str,
        security_envelope: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        probe_metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "upload_media", source_name)
        require_idempotency_key(idempotency_key)
        staged = copy_media_upload(self.root, source_name, file_name, source)
        try:
            return self._finish_media_upload(
                ctx,
                source_name,
                display_name,
                media_set_id,
                logical_path,
                staged,
                supplied_mime_type,
                schema_type,
                format,
                security_envelope,
                probe_metadata,
                idempotency_key,
            )
        finally:
            cleanup_media_upload(staged)

    def _finish_media_upload(
        self,
        ctx: RequestContext,
        source_name: str,
        display_name: str,
        media_set_id: str,
        logical_path: str,
        staged: StagedMediaSourceUpload,
        supplied_mime_type: str,
        schema_type: str,
        format: str,
        security_envelope: Mapping[str, object],
        probe_metadata: Mapping[str, object] | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        row, is_replayed = self._create_media_source(
            ctx, source_name, display_name, media_set_id, logical_path, staged.content_hash, staged.byte_size
        )
        media_result = commit_staged_media(
            self.media_transaction_service,
            self.media_upload_service,
            ctx,
            row,
            staged,
            logical_path,
            supplied_mime_type,
            schema_type,
            format,
            security_envelope,
            probe_metadata,
            idempotency_key,
        )
        return record_media_commit(
            self.engine,
            self.source_registry_repository,
            ctx,
            row["source_name"],
            media_result,
            is_replayed,
        )

    def _create_source(
        self,
        ctx: RequestContext,
        record: SourceConnectionRecord,
    ) -> tuple[SourceConnectionRow, bool]:
        with self.engine.begin() as conn:
            existing = self.source_registry_repository.source_by_name(
                transaction=conn, tenant_id=ctx.tenant_id, source_name=record.source_name
            )
            if existing is not None:
                require_same_config(existing, record)
                return existing, True
            try:
                self.source_registry_repository.create_source(transaction=conn, record=record)
            except SourceConnectionAlreadyExistsError as exc:
                raise ConflictDetected("source already exists", details={"source_name": record.source_name}) from exc
            row = source_row_in_tx(self.source_registry_repository, conn, ctx, record.source_name)
            upsert_source_resource(conn, ctx, self.resource_catalog_repository, self.runtime_service, row)
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="source.created",
                resource_type="source",
                resource_id=row["source_name"],
                action="create",
                after_ref=source_view(row),
            )
            return row, False

    def _create_upload_source(
        self,
        ctx: RequestContext,
        source_name: str,
        display_name: str,
        staged: Sequence[StagedSourceUpload],
        sync_name: str | None,
        primary_key: Sequence[str],
    ) -> tuple[SourceConnectionRow, bool]:
        upload = staged[0]
        summary = upload_summary("csv_upload", staged, {"syncName": sync_name, "primaryKey": list(primary_key)})
        record = source_record(
            ctx,
            source_name=source_name,
            display_name=display_name,
            kind="csv_upload",
            target_dataset_ref=upload.dataset_ref,
            target_media_set_id=None,
            config_summary=summary,
        )
        return self._create_source(ctx, record)

    def _create_batch_source(
        self,
        ctx: RequestContext,
        source_name: str,
        display_name: str,
        staged: Sequence[StagedSourceUpload],
        sync_name: str | None,
    ) -> tuple[SourceConnectionRow, bool]:
        dataset_refs = [upload.dataset_ref for upload in staged]
        summary = upload_summary("batch_file", staged, {"syncName": sync_name, "datasetRefs": dataset_refs})
        record = source_record(
            ctx,
            source_name=source_name,
            display_name=display_name,
            kind="batch_file",
            target_dataset_ref=dataset_refs[0] if dataset_refs else None,
            target_media_set_id=None,
            config_summary=summary,
        )
        return self._create_source(ctx, record)

    def _create_media_source(
        self,
        ctx: RequestContext,
        source_name: str,
        display_name: str,
        media_set_id: str,
        logical_path: str,
        content_hash: str,
        byte_size: int,
    ) -> tuple[SourceConnectionRow, bool]:
        summary = dict(mediaSetId=media_set_id, logicalPath=logical_path, contentHash=content_hash, byteSize=byte_size)
        record = source_record(
            ctx,
            source_name=source_name,
            display_name=display_name,
            kind="media_upload",
            target_dataset_ref=None,
            target_media_set_id=media_set_id,
            config_summary=summary,
        )
        return self._create_source(ctx, record)
