from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ResourceCatalogRepository,
    RuntimeRepository,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    SourceRegistryRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.services import source_onboarding_cdc_index as cdc_index
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.connector_onboarding_service import ConnectorOnboardingService
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.object_store.indexing_cdc_service import ObjectCdcIndexingService
from foundry_lite.application.services.source_managed_registry import (
    ManagedSyncPreparationDependencies,
    prepare_managed_sync_record,
)
from foundry_lite.application.services.source_management_config import (
    SOURCE_TEMPLATES,
    agent_record,
    credential_record,
    network_policy_record,
    require_idempotency_key,
    source_sync_run_record,
)
from foundry_lite.application.services.source_management_helpers import (
    SourceRuntimeBoundary,
    agent_row,
    create_credential_row,
    create_network_policy_row,
    create_sync_row,
    credential_row,
    mapping,
    now,
    require_same_fingerprint,
    require_write,
    run_row,
    secret_version,
    sync_row,
)
from foundry_lite.application.services.source_management_run_helpers import (
    SourceExplorationDependencies,
    complete_database_run,
    complete_rest_run,
    complete_stream_run,
    execute_source_exploration,
    fail_run,
    finish_run,
)
from foundry_lite.application.services.source_management_views import (
    agent_view,
    credential_view,
    network_policy_view,
    sync_run_list_view,
    sync_run_view,
    sync_view,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, ValidationFailed


class SourceManagementService(CoreService):
    """Palantir-style Source Wizard, Exploration, and Managed Sync control-plane."""

    required_dependencies = (
        "engine",
        "policy",
        "connector_adapter",
        "source_database_adapter",
        "source_management_repository",
        "source_stream_adapter",
        "source_registry_repository",
        "resource_catalog_repository",
        "secret_vault",
    )
    required_collaborators = (
        "connector_onboarding_service",
        "dataset_ingest_service",
        "dataset_registry_service",
        "runtime_service",
    )
    connector_adapter: ConnectorAdapter
    source_database_adapter: SourceDatabaseAdapter
    source_management_repository: SourceManagementRepository
    source_registry_repository: SourceRegistryRepository
    resource_catalog_repository: ResourceCatalogRepository
    connector_onboarding_service: ConnectorOnboardingService
    dataset_ingest_service: DatasetIngestService
    dataset_registry_service: DatasetRegistryService
    runtime_service: SourceRuntimeBoundary

    def list_templates(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        self.policy.require(ctx or RequestContext(), "source:read")
        return [dict(template) for template in SOURCE_TEMPLATES]

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
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "create_credential", credential_name)
        require_idempotency_key(idempotency_key)
        resolved_secret_name = secret_name or f"source_{credential_name}"
        resolved_secret_version = secret_version(secret_value)
        record = credential_record(
            ctx,
            credential_name=credential_name,
            display_name=display_name,
            kind=kind,
            auth_scheme=auth_scheme,
            secret_name=resolved_secret_name,
            secret_version=resolved_secret_version,
        )
        row = create_credential_row(
            self, self.runtime_service, ctx, credential_name, resolved_secret_name, secret_value, record
        )
        return credential_view(row)

    def list_credentials(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        rows = self.source_management_repository.list_credentials(tenant_id=ctx.tenant_id)
        return [credential_view(row) for row in rows]

    def get_credential(self, credential_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        with self.engine.begin() as conn:
            return credential_view(credential_row(self, conn, ctx, credential_name))

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
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "register_agent", agent_id)
        require_idempotency_key(idempotency_key)
        record = agent_record(
            ctx,
            agent_id=agent_id,
            display_name=display_name,
            mode=mode,
            capabilities=capabilities,
            network_summary=network_summary,
        )
        with self.engine.begin() as conn:
            existing = self.source_management_repository.agent_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, agent_id=agent_id
            )
            if existing is not None:
                require_same_fingerprint(existing, record.config_fingerprint)
                return agent_view(existing)
            self.source_management_repository.create_agent(transaction=conn, record=record)
            row = agent_row(self, conn, ctx, agent_id)
            self._audit_source_agent_registered(conn, ctx, agent_id, row)
        return agent_view(row)

    def _audit_source_agent_registered(
        self, conn: TransactionContext, ctx: RequestContext, agent_id: str, row: Mapping[str, object]
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="source.agent.registered",
            resource_type="source_agent",
            resource_id=agent_id,
            action="source_manage",
            after_ref=agent_view(row),
        )

    def list_agents(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        return [agent_view(row) for row in self.source_management_repository.list_agents(tenant_id=ctx.tenant_id)]

    def heartbeat_agent(self, agent_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "heartbeat_agent", agent_id)
        with self.engine.begin() as conn:
            row = self.source_management_repository.update_agent_heartbeat(
                transaction=conn, tenant_id=ctx.tenant_id, agent_id=agent_id, heartbeat_at=now()
            )
        if row is None:
            raise NotFound("source agent not found", details={"agent_id": agent_id})
        return agent_view(row)

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
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "create_network_policy", policy_name)
        require_idempotency_key(idempotency_key)
        record = network_policy_record(
            ctx,
            policy_name=policy_name,
            display_name=display_name,
            mode=mode,
            agent_id=agent_id,
            allowed_hosts=allowed_hosts,
        )
        row = create_network_policy_row(self, self.runtime_service, ctx, policy_name, mode, agent_id, record)
        return network_policy_view(row)

    def list_network_policies(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        rows = self.source_management_repository.list_network_policies(tenant_id=ctx.tenant_id)
        return [network_policy_view(row) for row in rows]

    def explore_source(
        self,
        *,
        source_name: str,
        source_type: str,
        request: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "explore_source", source_name)
        return execute_source_exploration(
            self,
            self.runtime_service,
            self._exploration_dependencies(),
            ctx,
            source_name=source_name,
            source_type=source_type,
            request=request,
        )

    def _exploration_dependencies(self) -> SourceExplorationDependencies:
        return SourceExplorationDependencies(
            engine=self.engine,
            connector_adapter=self.connector_adapter,
            connector_onboarding_service=self.connector_onboarding_service,
            source_database_adapter=self.source_database_adapter,
            source_management_repository=self.source_management_repository,
            source_registry_repository=self.source_registry_repository,
            source_stream_adapter=self.source_stream_adapter,
            secret_vault=self.secret_vault,
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
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "create_managed_sync", sync_name)
        require_idempotency_key(idempotency_key)
        record = prepare_managed_sync_record(
            self._managed_sync_dependencies(),
            self.runtime_service,
            ctx,
            sync_name=sync_name,
            source_name=source_name,
            display_name=display_name,
            source_type=source_type,
            capability=capability,
            target_dataset_ref=target_dataset_ref,
            target_media_set_id=target_media_set_id,
            mode=mode,
            schedule=schedule,
            config_summary=config_summary,
        )
        row = create_sync_row(self, self.runtime_service, ctx, sync_name, record)
        return sync_view(row)

    def _managed_sync_dependencies(self) -> ManagedSyncPreparationDependencies:
        return ManagedSyncPreparationDependencies(
            engine=self.engine,
            source_registry_repository=self.source_registry_repository,
            resource_catalog_repository=self.resource_catalog_repository,
            dataset_registry_service=self.dataset_registry_service,
        )

    def list_managed_syncs(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        return [sync_view(row) for row in self.source_management_repository.list_syncs(tenant_id=ctx.tenant_id)]

    def get_managed_sync(self, sync_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        with self.engine.begin() as conn:
            return sync_view(sync_row(self, conn, ctx, sync_name))

    def start_managed_sync_run(
        self,
        sync_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        trigger_type: str = "manual",
        batch_limit: int | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "start_managed_sync_run", sync_name)
        require_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            sync = sync_row(self, conn, ctx, sync_name)
            replay = self.source_management_repository.sync_run_by_idempotency_key(
                transaction=conn, tenant_id=ctx.tenant_id, sync_name=sync_name, idempotency_key=idempotency_key
            )
            if replay is not None:
                return sync_run_view(replay)
            self._require_source_active(conn, ctx, sync)
            run = self._create_run_row(conn, ctx, sync, trigger_type, idempotency_key, batch_limit)
        return self._execute_run(ctx, sync, run)

    def _require_source_active(self, conn: TransactionContext, ctx: RequestContext, sync: Mapping[str, object]) -> None:
        source = self.source_registry_repository.source_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=str(sync["source_name"]),
        )
        if source is not None and source["status"] == "disabled":
            raise ValidationFailed(
                "source is disabled",
                details={"sourceName": source["source_name"], "syncName": sync["sync_name"]},
            )

    def list_managed_sync_runs(self, sync_name: str, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        rows = self.source_management_repository.list_sync_runs(tenant_id=ctx.tenant_id, sync_name=sync_name)
        return sync_run_list_view(rows)

    def get_managed_sync_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        with self.engine.begin() as conn:
            row = self.source_management_repository.sync_run_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, run_id=run_id
            )
        if row is None:
            raise NotFound("source sync run not found", details={"run_id": run_id})
        return sync_run_view(row)

    def _create_run_row(
        self,
        conn: object,
        ctx: RequestContext,
        sync: Mapping[str, object],
        trigger_type: str,
        idempotency_key: str,
        batch_limit: int | None,
    ) -> Mapping[str, object]:
        record = source_sync_run_record(
            ctx,
            sync,
            trigger_type=trigger_type,
            idempotency_key=idempotency_key,
            batch_limit=batch_limit,
            checkpoint_start=mapping(sync.get("checkpoint")),
        )
        self.source_management_repository.create_sync_run(transaction=conn, record=record)
        return run_row(self, conn, ctx, record.id)

    def _execute_run(
        self, ctx: RequestContext, sync: Mapping[str, object], run: Mapping[str, object]
    ) -> dict[str, object]:
        try:
            if sync["source_type"] in {"rest_api", "sap_odata"}:
                return complete_rest_run(self, self.connector_onboarding_service, ctx, sync, run)
            if sync["source_type"] == "postgres_jdbc":
                return complete_database_run(self, self.dataset_ingest_service, ctx, sync, run)
            if sync["source_type"] == "kafka":
                completion = complete_stream_run(self, self.dataset_ingest_service, ctx, sync, run)
                return finish_run(
                    self,
                    ctx,
                    sync,
                    run,
                    "succeeded",
                    None,
                    completion.dataset_version_id,
                    completion.checkpoint,
                    completion.result,
                    None,
                )
            raise ValidationFailed(
                "managed sync data-plane is not available", details={"sourceType": sync["source_type"]}
            )
        except FoundryLiteError as exc:
            return fail_run(self, ctx, run, exc)


class SourceCdcObjectIndexService(CoreService):
    """Runs one bounded CDC dataset-version-to-object-index tick from product surfaces."""

    required_dependencies = ("engine", "policy", "runtime_repository", "source_registry_repository")
    required_collaborators = ("dataset_registry_service", "object_cdc_indexing_service", "runtime_service")
    engine: TransactionManager
    runtime_repository: RuntimeRepository
    source_registry_repository: SourceRegistryRepository
    dataset_registry_service: cdc_index.CdcObjectIndexDatasetBoundary
    object_cdc_indexing_service: ObjectCdcIndexingService
    runtime_service: SourceRuntimeBoundary

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
        runtime = cdc_index.CdcObjectIndexRuntime(
            policy=self.policy,
            runtime_service=self.runtime_service,
            engine=self.engine,
            runtime_repository=self.runtime_repository,
            source_registry_repository=self.source_registry_repository,
            dataset_registry_service=self.dataset_registry_service,
            object_cdc_indexing_service=self.object_cdc_indexing_service,
        )
        return cdc_index.start_debezium_object_index(
            runtime,
            source_name,
            object_type_api_name,
            idempotency_key,
            ctx,
            expected_config_fingerprint,
            max_rows_per_version,
        )
