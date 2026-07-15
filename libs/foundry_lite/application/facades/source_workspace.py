"""Thin facade entrypoints for source workspace workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import BinaryIO

from foundry_lite.application.facades.source_workspace_diagnostics import SourceWorkspaceDiagnostics
from foundry_lite.application.facades.source_workspace_services import SourceWorkspaceServices
from foundry_lite.application.services.source_onboarding_config import SourceUpload
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class SourceWorkspace(SourceWorkspaceDiagnostics):
    """Product-facing Source onboarding facade for browser and SDK flows."""

    def __init__(self, services: SourceWorkspaceServices) -> None:
        super().__init__(services.source_connection_test, services.source_lifecycle)
        self._onboarding = services.source_onboarding
        self._management = services.source_management
        self._lifecycle = services.source_lifecycle
        self._scheduler = services.source_scheduler
        self._cdc_object_index = services.source_cdc_object_index

    def list_templates(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._management.list_templates(ctx=ctx)

    def create_credential(
        self,
        *,
        credential_name: str,
        display_name: str,
        kind: str,
        auth_scheme: str,
        secret_value: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        secret_name: str | None = None,
    ) -> dict[str, object]:
        return self._management.create_credential(
            credential_name=credential_name,
            display_name=display_name,
            kind=kind,
            auth_scheme=auth_scheme,
            secret_value=secret_value,
            idempotency_key=idempotency_key,
            ctx=ctx,
            secret_name=secret_name,
        )

    def list_credentials(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._management.list_credentials(ctx=ctx)

    def get_credential(self, credential_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._management.get_credential(credential_name, ctx=ctx)

    def list_sources(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._onboarding.list_sources(ctx=ctx)

    def get_source(self, source_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._onboarding.get_source(source_name, ctx=ctx)

    def update_source_status(
        self,
        source_name: str,
        *,
        status: str,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._lifecycle.update_source_status(
            source_name,
            status=status,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def register_agent(
        self,
        *,
        agent_id: str,
        display_name: str,
        mode: str,
        capabilities: Mapping[str, object],
        network_summary: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._management.register_agent(
            agent_id=agent_id,
            display_name=display_name,
            mode=mode,
            capabilities=capabilities,
            network_summary=network_summary,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_agents(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._management.list_agents(ctx=ctx)

    def heartbeat_agent(self, agent_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._management.heartbeat_agent(agent_id, ctx=ctx)

    def create_network_policy(
        self,
        *,
        policy_name: str,
        display_name: str,
        mode: str,
        allowed_hosts: Sequence[str],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        agent_id: str | None = None,
    ) -> dict[str, object]:
        return self._management.create_network_policy(
            policy_name=policy_name,
            display_name=display_name,
            mode=mode,
            allowed_hosts=allowed_hosts,
            idempotency_key=idempotency_key,
            ctx=ctx,
            agent_id=agent_id,
        )

    def list_network_policies(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._management.list_network_policies(ctx=ctx)

    def explore_source(
        self,
        *,
        source_name: str,
        source_type: str,
        request: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._management.explore_source(
            source_name=source_name, source_type=source_type, request=request, ctx=ctx
        )

    def create_managed_sync(
        self,
        *,
        sync_name: str,
        source_name: str,
        display_name: str,
        source_type: str,
        capability: str,
        mode: str,
        config_summary: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        target_dataset_ref: str | None = None,
        target_media_set_id: str | None = None,
        schedule: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self._management.create_managed_sync(
            sync_name=sync_name,
            source_name=source_name,
            display_name=display_name,
            source_type=source_type,
            capability=capability,
            mode=mode,
            config_summary=config_summary,
            idempotency_key=idempotency_key,
            ctx=ctx,
            target_dataset_ref=target_dataset_ref,
            target_media_set_id=target_media_set_id,
            schedule=schedule,
        )

    def list_managed_syncs(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._management.list_managed_syncs(ctx=ctx)

    def get_managed_sync(self, sync_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._management.get_managed_sync(sync_name, ctx=ctx)

    def start_managed_streaming_sync(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._lifecycle.start_managed_streaming_sync(
            sync_name,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def stop_managed_streaming_sync(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._lifecycle.stop_managed_streaming_sync(
            sync_name,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def update_managed_sync_schedule(
        self,
        sync_name: str,
        *,
        schedule: Mapping[str, object],
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._scheduler.update_managed_sync_schedule(
            sync_name,
            schedule=schedule,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def pause_managed_sync_schedule(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._scheduler.pause_managed_sync_schedule(
            sync_name,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def resume_managed_sync_schedule(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._scheduler.resume_managed_sync_schedule(
            sync_name,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def start_managed_sync_run(
        self,
        sync_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        trigger_type: str = "manual",
        batch_limit: int | None = None,
    ) -> dict[str, object]:
        return self._scheduler.start_managed_sync_run(
            sync_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            trigger_type=trigger_type,
            batch_limit=batch_limit,
        )

    def list_managed_sync_runs(self, sync_name: str, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._management.list_managed_sync_runs(sync_name, ctx=ctx)

    def get_managed_sync_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._management.get_managed_sync_run(run_id, ctx=ctx)

    def preview_due_managed_syncs(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self._scheduler.preview_due_managed_syncs(ctx=ctx, max_runs=max_runs)

    def run_due_managed_syncs(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self._scheduler.run_due_managed_syncs(ctx=ctx, max_runs=max_runs)

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
        return self._onboarding.upload_csv(
            source_name=source_name,
            display_name=display_name,
            dataset_ref=dataset_ref,
            file_name=file_name,
            source=source,
            idempotency_key=idempotency_key,
            ctx=ctx,
            sync_name=sync_name,
            primary_key=primary_key,
        )

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
        return self._onboarding.upload_batch_files(
            source_name=source_name,
            display_name=display_name,
            uploads=uploads,
            idempotency_key=idempotency_key,
            ctx=ctx,
            sync_name=sync_name,
        )

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
        return self._onboarding.create_webhook_listener(
            source_name=source_name,
            display_name=display_name,
            dataset_ref=dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            signing_secret_ref=signing_secret_ref,
            inbound_url=inbound_url,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

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
        return self._onboarding.ingest_webhook_listener_event(
            source_name,
            payload=payload,
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            event_id=event_id,
            require_service_principal_signature=require_service_principal_signature,
            ctx=ctx,
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
        primary_key: Sequence[str] = (),
    ) -> dict[str, object]:
        return self._onboarding.create_debezium_source(
            source_name=source_name,
            display_name=display_name,
            dataset_ref=dataset_ref,
            stream_name=stream_name,
            topic=topic,
            idempotency_key=idempotency_key,
            ctx=ctx,
            consumer_group=consumer_group,
            secret_refs=secret_refs,
            primary_key=primary_key,
        )

    def debezium_operation_plan(
        self,
        source_name: str,
        *,
        ctx: RequestContext | None = None,
        object_type_api_name: str = "Order",
    ) -> dict[str, object]:
        return self._onboarding.debezium_operation_plan(
            source_name,
            ctx=ctx,
            object_type_api_name=object_type_api_name,
        )

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
        return self._onboarding.start_debezium_sync(
            source_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            expected_config_fingerprint=expected_config_fingerprint,
            after_offset=after_offset,
            limit=limit,
        )

    def start_debezium_object_index(
        self,
        source_name: str,
        *,
        object_type_api_name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        expected_config_fingerprint: str | None = None,
        max_rows_per_version: int = 10_000,
    ) -> dict[str, object]:
        return self._cdc_object_index.start_debezium_object_index(
            source_name,
            object_type_api_name=object_type_api_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            expected_config_fingerprint=expected_config_fingerprint,
            max_rows_per_version=max_rows_per_version,
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
        return self._onboarding.upload_media(
            source_name=source_name,
            display_name=display_name,
            media_set_id=media_set_id,
            logical_path=logical_path,
            file_name=file_name,
            source=source,
            supplied_mime_type=supplied_mime_type,
            schema_type=schema_type,
            format=format,
            security_envelope=security_envelope,
            idempotency_key=idempotency_key,
            ctx=ctx,
            probe_metadata=probe_metadata,
        )
