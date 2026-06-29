from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorSnapshotRequest,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.connector_onboarding_service import ConnectorOnboardingService
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.source_management_config import (
    SOURCE_TEMPLATES,
    agent_record,
    credential_record,
    exploration_run_record,
    network_policy_record,
    require_idempotency_key,
    source_sync_record,
    source_sync_run_record,
)
from foundry_lite.application.services.source_management_helpers import (
    SourceRuntimeBoundary,
    agent_row,
    audit,
    commit_result_payload,
    create_credential_row,
    create_network_policy_row,
    create_sync_row,
    credential_row,
    fieldnames,
    int_value,
    mapping,
    now,
    optional_text,
    require_same_fingerprint,
    require_write,
    required_text,
    rest_source_config,
    run_row,
    secret_version,
    sync_row,
    target_dataset_ref,
    text,
)
from foundry_lite.application.services.source_management_views import (
    agent_view,
    credential_view,
    exploration_view,
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
        result = self._explore_source_payload(ctx, source_name, source_type, request)
        with self.engine.begin() as conn:
            record = exploration_run_record(
                ctx,
                source_name=source_name,
                source_type=source_type,
                request=request,
                result_summary=result,
                status="succeeded",
            )
            self.source_management_repository.create_exploration_run(transaction=conn, record=record)
            audit(
                self.runtime_service,
                conn,
                ctx,
                "source.explored",
                "source",
                source_name,
                exploration_view(record.__dict__),
            )
        return exploration_view(record.__dict__)

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
        if target_dataset_ref is not None:
            self.dataset_registry_service.ensure_dataset(target_dataset_ref, ctx=ctx)
        record = source_sync_record(
            ctx,
            sync_name=sync_name,
            source_name=source_name,
            display_name=display_name,
            source_type=source_type,
            capability=capability,
            target_dataset_ref=target_dataset_ref,
            target_media_set_id=target_media_set_id,
            mode=mode,
            schedule=schedule or {},
            config_summary=config_summary,
        )
        row = create_sync_row(self, self.runtime_service, ctx, sync_name, record)
        return sync_view(row)

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
            run = self._create_run_row(conn, ctx, sync, trigger_type, idempotency_key, batch_limit)
        return self._execute_run(ctx, sync, run)

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

    def _explore_source_payload(
        self, ctx: RequestContext, source_name: str, source_type: str, request: Mapping[str, object]
    ) -> dict[str, object]:
        if source_type == "rest_api":
            return self._explore_rest(ctx, source_name, request)
        if source_type == "postgres_jdbc":
            return self._explore_database(request)
        raise ValidationFailed("source type does not support exploration", details={"sourceType": source_type})

    def _explore_rest(self, ctx: RequestContext, source_name: str, request: Mapping[str, object]) -> dict[str, object]:
        snapshot = self.connector_adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name=source_name,
                resource_name=text(request, "resourceName", "preview"),
                tenant_id=ctx.tenant_id,
                request_id=ctx.request_id,
                rest=rest_source_config(request),
            )
        )
        return {"sample": list(snapshot.rows), "schema": dict(snapshot.schema), "cursor": dict(snapshot.cursor or {})}

    def _explore_database(self, request: Mapping[str, object]) -> dict[str, object]:
        database_url = self.secret_vault.get_secret(required_text(request, "databaseUrlSecretRef")).value
        table_name = optional_text(request.get("tableName"))
        sample_limit = int_value(request.get("sampleLimit"), 20)
        if table_name is None:
            tables = self.source_database_adapter.list_tables(database_url, sample_limit=sample_limit)
            return {"tables": [dict(table) for table in tables]}
        batch = self.source_database_adapter.read_table_batch(
            database_url,
            table_name=table_name,
            batch_limit=sample_limit,
            checkpoint_column=optional_text(request.get("checkpointColumn")),
        )
        return {"sample": list(batch.rows), "schema": dict(batch.schema), "checkpoint": dict(batch.checkpoint)}

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
            if sync["source_type"] == "rest_api":
                return self._complete_rest_run(ctx, sync, run)
            if sync["source_type"] == "postgres_jdbc":
                return self._complete_database_run(ctx, sync, run)
            raise ValidationFailed(
                "managed sync data-plane is not available", details={"sourceType": sync["source_type"]}
            )
        except FoundryLiteError as exc:
            return self._fail_run(ctx, run, exc)

    def _complete_rest_run(
        self, ctx: RequestContext, sync: Mapping[str, object], run: Mapping[str, object]
    ) -> dict[str, object]:
        summary = mapping(sync["config_summary"])
        workflow = self.connector_onboarding_service.start_resource_sync(
            required_text(summary, "connectorName"),
            required_text(summary, "resourceName"),
            idempotency_key=str(run["idempotency_key"]),
            ctx=ctx,
            sync_name=str(sync["sync_name"]),
            transaction_type=str(sync["mode"]),
        )
        result = {"workflowRun": dict(workflow)}
        return self._finish_run(ctx, sync, run, "running", workflow["workflowRunId"], None, {}, result, None)

    def _complete_database_run(
        self, ctx: RequestContext, sync: Mapping[str, object], run: Mapping[str, object]
    ) -> dict[str, object]:
        summary = mapping(sync["config_summary"])
        dataset_ref = target_dataset_ref(sync)
        database_url = self.secret_vault.get_secret(required_text(summary, "databaseUrlSecretRef")).value
        batch = self.source_database_adapter.read_table_batch(
            database_url,
            table_name=required_text(summary, "tableName"),
            batch_limit=int_value(run.get("batch_limit") or summary.get("batchLimit"), 100),
            checkpoint_column=optional_text(summary.get("checkpointColumn")),
            after_value=mapping(run["checkpoint_start"]).get("lastValue"),
        )
        commit = self.dataset_ingest_service.sync_rows_batch(
            dataset_ref,
            batch.rows,
            fieldnames=fieldnames(batch.rows, batch.schema),
            ctx=ctx,
            sync_name=str(sync["sync_name"]),
            tx_type=str(sync["mode"]),
            source_type=f"source.{sync['source_type']}",
            transaction_metadata={"sourceManagedSync": {"runId": run["id"], "checkpoint": dict(batch.checkpoint)}},
        )
        result = commit_result_payload(commit, batch)
        return self._finish_run(
            ctx, sync, run, "succeeded", None, result.get("datasetVersionId"), batch.checkpoint, result, None
        )

    def _finish_run(
        self,
        ctx: RequestContext,
        sync: Mapping[str, object],
        run: Mapping[str, object],
        status: str,
        workflow_run_id: str | None,
        dataset_version_id: object,
        checkpoint: Mapping[str, object],
        result: Mapping[str, object],
        error: Mapping[str, object] | None,
    ) -> dict[str, object]:
        completed_at = now()
        with self.engine.begin() as conn:
            updated = self.source_management_repository.update_sync_run_result(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=str(run["id"]),
                status=status,
                dataset_version_id=dataset_version_id if isinstance(dataset_version_id, str) else None,
                checkpoint_end=checkpoint,
                result_summary=result,
                error=error,
                completed_at=completed_at,
            )
            self.source_management_repository.update_sync_after_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                sync_name=str(sync["sync_name"]),
                run_id=str(run["id"]),
                workflow_run_id=workflow_run_id,
                checkpoint=checkpoint,
                updated_at=completed_at,
            )
        return sync_run_view(updated or run)

    def _fail_run(self, ctx: RequestContext, run: Mapping[str, object], exc: FoundryLiteError) -> dict[str, object]:
        error = {"message": exc.message, "details": dict(exc.details)}
        sync = {"sync_name": run["sync_name"]}
        return self._finish_run(ctx, sync, run, "failed", None, None, {}, {}, error)
