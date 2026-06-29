from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorConnectionAlreadyExistsError,
    ConnectorConnectionRecord,
    ConnectorConnectionRow,
    ConnectorRegistryRepository,
    ConnectorResourceRow,
    DatasetRow,
    ProductWorkflowRun,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.connector_onboarding_config import (
    ConnectorBundle,
    _activity_connector_input,
    _connection_patch,
    _connection_record,
    _optional_text,
    _require_idempotency_key,
    _resolved_config_fingerprint,
    _resource_record,
    _rest_source,
    _snapshot_request,
)
from foundry_lite.application.services.connector_onboarding_views import (
    _activity_result,
    _connection_audit_ref,
    _connection_view,
    _error_payload,
    _require_compatible_primary_key,
    _require_same_config,
    _resource_view,
    _test_result,
)
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.application.services.workflow_orchestration_service import WorkflowOrchestrationService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed


class ConnectorOnboardingService(CoreService):
    """Product surface for registering REST connector configs and starting syncs."""

    required_dependencies = ("engine", "policy", "connector_registry_repository", "connector_adapter")
    required_collaborators = (
        "dataset_ingest_service",
        "dataset_registry_service",
        "runtime_service",
        "workflow_orchestration_service",
    )
    connector_registry_repository: ConnectorRegistryRepository
    connector_adapter: ConnectorAdapter
    dataset_ingest_service: DatasetIngestService
    dataset_registry_service: DatasetRegistryService
    runtime_service: RuntimeService
    workflow_orchestration_service: WorkflowOrchestrationService

    def create_connection(
        self,
        *,
        connector_name: str,
        display_name: str,
        base_url: str,
        auth: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        rate_limit_per_minute: int | None = None,
        allow_private_network: bool = False,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self._require_write(ctx, "create_connection", connector_name)
        _require_idempotency_key(idempotency_key)
        record = _connection_record(
            ctx,
            connector_name,
            display_name,
            base_url,
            auth,
            rate_limit_per_minute,
            allow_private_network,
        )
        with self.engine.begin() as conn:
            existing = self.connector_registry_repository.connection_by_name(
                transaction=conn, tenant_id=ctx.tenant_id, connector_name=record.connector_name
            )
            row = self._create_or_replay_connection(conn, ctx, record, existing, idempotency_key)
            resources = self.connector_registry_repository.resources_for_connection(
                transaction=conn, tenant_id=ctx.tenant_id, connector_name=record.connector_name
            )
        return _connection_view(row, resources)

    def list_connections(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "connector:read")
        rows = self.connector_registry_repository.list_connections(tenant_id=ctx.tenant_id)
        return [self._connection_with_resources(ctx, row) for row in rows]

    def get_connection(self, connector_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "connector:read")
        with self.engine.begin() as conn:
            row = self._connection_row(conn, ctx, connector_name)
            resources = self.connector_registry_repository.resources_for_connection(
                transaction=conn, tenant_id=ctx.tenant_id, connector_name=connector_name
            )
        return _connection_view(row, resources)

    def update_connection(
        self,
        connector_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        display_name: str | None = None,
        base_url: str | None = None,
        auth: Mapping[str, object] | None = None,
        rate_limit_per_minute: int | None = None,
        allow_private_network: bool | None = None,
        status: str | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self._require_write(ctx, "update_connection", connector_name)
        _require_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            current = self._connection_row(conn, ctx, connector_name)
            patch = _connection_patch(
                current,
                display_name,
                base_url,
                auth,
                rate_limit_per_minute,
                allow_private_network,
                status,
            )
            row = self.connector_registry_repository.update_connection(
                transaction=conn, tenant_id=ctx.tenant_id, connector_name=connector_name, patch=patch
            )
            self._audit_connection_updated(conn, ctx, row or current, idempotency_key)
            resources = self.connector_registry_repository.resources_for_connection(
                transaction=conn, tenant_id=ctx.tenant_id, connector_name=connector_name
            )
        return _connection_view(row or current, resources)

    def upsert_resource(
        self,
        connector_name: str,
        resource_name: str,
        *,
        dataset_ref: str,
        resource_path: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        pagination: Mapping[str, object] | None = None,
        schema_columns: Sequence[str] = (),
        primary_key: Sequence[str] = (),
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self._require_write(ctx, "upsert_resource", f"{connector_name}/{resource_name}")
        _require_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            connection = self._connection_row(conn, ctx, connector_name)
            self._ensure_target_dataset(ctx, dataset_ref, primary_key)
            record = _resource_record(
                ctx,
                connector_name,
                resource_name,
                dataset_ref,
                resource_path,
                pagination,
                schema_columns,
                primary_key,
            )
            row = self.connector_registry_repository.upsert_resource(transaction=conn, record=record)
            self._audit_resource_upserted(conn, ctx, connection, row, idempotency_key)
        return _resource_view(connection, row)

    def test_resource(
        self, connector_name: str, resource_name: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self._require_write(ctx, "test_resource", f"{connector_name}/{resource_name}")
        bundle = self._resource_bundle(ctx, connector_name, resource_name)
        try:
            snapshot = self.connector_adapter.snapshot(_snapshot_request(ctx, bundle))
        except FoundryLiteError as exc:
            self._audit_resource_test(ctx, bundle, status="failed", error=_error_payload(exc))
            return _test_result(bundle, status="failed", error=_error_payload(exc))
        self._audit_resource_test(ctx, bundle, status="succeeded", error=None)
        return _test_result(
            bundle,
            status="succeeded",
            rows=snapshot.rows,
            schema=snapshot.schema,
            cursor=snapshot.cursor,
        )

    def start_resource_sync(
        self,
        connector_name: str,
        resource_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        transaction_type: str = "SNAPSHOT",
    ) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self._require_write(ctx, "start_resource_sync", f"{connector_name}/{resource_name}")
        _require_idempotency_key(idempotency_key)
        bundle = self._resource_bundle(ctx, connector_name, resource_name)
        self._ensure_target_dataset(ctx, bundle.resource["dataset_ref"], bundle.resource["primary_key"])
        return self.workflow_orchestration_service.start_connector_sync_workflow(
            bundle.resource["dataset_ref"],
            connector_name=connector_name,
            resource_name=resource_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            sync_name=sync_name,
            config_fingerprint=bundle.config_fingerprint,
            transaction_type=transaction_type,
        )

    def run_registered_sync_activity(
        self, payload: Mapping[str, object], *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        connector_name, resource_name, expected = _activity_connector_input(payload)
        bundle = self._resource_bundle(ctx, connector_name, resource_name)
        if expected != bundle.config_fingerprint:
            raise ConflictDetected(
                "connector config changed after workflow start",
                details={"connector_name": connector_name},
            )
        result = self.dataset_ingest_service.sync_connector_snapshot(
            bundle.resource["dataset_ref"],
            connector_name=connector_name,
            resource_name=resource_name,
            sync_name=_optional_text(payload.get("syncName")),
            ctx=ctx,
            tx_type=str(payload.get("transactionType") or "SNAPSHOT"),
            rest=bundle.rest,
        )
        return _activity_result(result, connector_name, resource_name, bundle.config_fingerprint)

    def _create_or_replay_connection(
        self,
        conn: object,
        ctx: RequestContext,
        record: ConnectorConnectionRecord,
        existing: ConnectorConnectionRow | None,
        idempotency_key: str,
    ) -> ConnectorConnectionRow:
        if existing is not None:
            _require_same_config(existing["config_fingerprint"], record.config_fingerprint)
            return existing
        try:
            self.connector_registry_repository.create_connection(transaction=conn, record=record)
        except ConnectorConnectionAlreadyExistsError as exc:
            raise ConflictDetected(
                "connector connection already exists",
                details={"connector_name": record.connector_name},
            ) from exc
        row = self._connection_row(conn, ctx, record.connector_name)
        self._audit_connection_created(conn, ctx, row, idempotency_key)
        return row

    def _connection_with_resources(self, ctx: RequestContext, row: ConnectorConnectionRow) -> dict[str, object]:
        with self.engine.begin() as conn:
            resources = self.connector_registry_repository.resources_for_connection(
                transaction=conn, tenant_id=ctx.tenant_id, connector_name=row["connector_name"]
            )
        return _connection_view(row, resources)

    def _resource_bundle(self, ctx: RequestContext, connector_name: str, resource_name: str) -> ConnectorBundle:
        with self.engine.begin() as conn:
            connection = self._connection_row(conn, ctx, connector_name)
            resource = self._resource_row(conn, ctx, connector_name, resource_name)
        if connection["status"] != "active":
            raise ValidationFailed("connector connection is not active", details={"connector_name": connector_name})
        return ConnectorBundle(
            connection,
            resource,
            _resolved_config_fingerprint(connection, resource),
            _rest_source(connection, resource),
        )

    def _connection_row(self, conn: object, ctx: RequestContext, connector_name: str) -> ConnectorConnectionRow:
        row = self.connector_registry_repository.connection_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, connector_name=connector_name
        )
        if row is None:
            raise NotFound("connector connection not found", details={"connector_name": connector_name})
        return row

    def _resource_row(
        self,
        conn: object,
        ctx: RequestContext,
        connector_name: str,
        resource_name: str,
    ) -> ConnectorResourceRow:
        row = self.connector_registry_repository.resource_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, connector_name=connector_name, resource_name=resource_name
        )
        if row is None:
            raise NotFound(
                "connector resource not found",
                details={"connector_name": connector_name, "resource_name": resource_name},
            )
        return row

    def _ensure_target_dataset(self, ctx: RequestContext, dataset_ref: str, primary_key: Sequence[str]) -> DatasetRow:
        existing = self.dataset_registry_service.find_dataset(dataset_ref, ctx=ctx)
        if existing is not None:
            _require_compatible_primary_key(existing, primary_key)
            return existing
        return self.dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx, primary_key=list(primary_key))

    def _require_write(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.policy.require(ctx, "connector:write")
        self.runtime_service._require_write_traffic_open(
            ctx, operation=operation, resource_type="connector", resource_id=resource_id
        )

    def _audit_connection_created(
        self,
        conn: object,
        ctx: RequestContext,
        row: ConnectorConnectionRow,
        idempotency_key: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="connector.connection.created",
            resource_type="connector",
            resource_id=row["connector_name"],
            action="create",
            after_ref=_connection_audit_ref(row, idempotency_key),
        )

    def _audit_connection_updated(
        self,
        conn: object,
        ctx: RequestContext,
        row: ConnectorConnectionRow,
        idempotency_key: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="connector.connection.updated",
            resource_type="connector",
            resource_id=row["connector_name"],
            action="update",
            after_ref=_connection_audit_ref(row, idempotency_key),
        )

    def _audit_resource_upserted(
        self,
        conn: object,
        ctx: RequestContext,
        connection: ConnectorConnectionRow,
        row: ConnectorResourceRow,
        idempotency_key: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="connector.resource.upserted",
            resource_type="connector_resource",
            resource_id=f"{connection['connector_name']}/{row['resource_name']}",
            action="upsert",
            after_ref={**_resource_view(connection, row), "idempotencyKey": idempotency_key},
        )

    def _audit_resource_test(
        self,
        ctx: RequestContext,
        bundle: ConnectorBundle,
        *,
        status: str,
        error: Mapping[str, object] | None,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="connector.resource.tested",
                resource_type="connector_resource",
                resource_id=f"{bundle.connection['connector_name']}/{bundle.resource['resource_name']}",
                action="test",
                after_ref={
                    "status": status,
                    "configFingerprint": bundle.config_fingerprint,
                    "error": dict(error or {}),
                },
            )
