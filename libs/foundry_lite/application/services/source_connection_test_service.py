"""Durable live-connection diagnostics for Data Connection Sources."""

from __future__ import annotations

import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from foundry_lite.application.ports import (
    ConnectorNetworkRoute,
    SourceConnectionRow,
    SourceConnectionTestAlreadyExistsError,
    SourceConnectionTestRecord,
    SourceConnectionTestRow,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    SourceRegistryRepository,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.connector_onboarding_service import ConnectorOnboardingService
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.application.services.source_connection_test_views import (
    SourceProbeOutcome,
    connection_checks,
    egress_attempt_view,
    stream_network_evidence,
)
from foundry_lite.application.services.source_management_config import require_idempotency_key
from foundry_lite.application.services.source_management_helpers import SourceRuntimeBoundary, require_write
from foundry_lite.application.services.source_management_streaming import source_stream_connection
from foundry_lite.application.services.source_network_routing import resolve_source_network_route
from foundry_lite.application.services.source_onboarding_helpers import source_row_in_tx
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, ValidationFailed


class SourceConnectionTestService(CoreService):
    """Tests a Source without creating a Dataset commit and persists operator evidence."""

    required_dependencies = (
        "engine",
        "policy",
        "secret_vault",
        "source_database_adapter",
        "source_management_repository",
        "source_registry_repository",
        "source_stream_adapter",
    )
    required_collaborators = ("connector_onboarding_service", "runtime_service")
    source_database_adapter: SourceDatabaseAdapter
    source_management_repository: SourceManagementRepository
    source_registry_repository: SourceRegistryRepository
    connector_onboarding_service: ConnectorOnboardingService
    runtime_service: SourceRuntimeBoundary

    def test_source_connection(
        self,
        source_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "test_source_connection", source_name)
        require_idempotency_key(idempotency_key)
        source, record, replay = self._start_or_replay(ctx, source_name, expected_config_fingerprint, idempotency_key)
        if replay is not None:
            return connection_test_view(replay, is_replayed=True)
        try:
            outcome = self._probe_source(ctx, source)
        except FoundryLiteError as exc:
            return self._complete(ctx, record, source, outcome=None, error=exc)
        return self._complete(ctx, record, source, outcome=outcome, error=None)

    def list_source_connection_tests(
        self,
        source_name: str,
        *,
        limit: int = 20,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        _require_history_limit(limit)
        with self.engine.begin() as conn:
            source_row_in_tx(self.source_registry_repository, conn, ctx, source_name)
        rows = self.source_registry_repository.list_connection_tests(
            tenant_id=ctx.tenant_id, source_name=source_name, limit=limit
        )
        return [connection_test_view(row) for row in rows]

    def list_source_egress_attempts(
        self,
        source_name: str,
        *,
        limit: int = 20,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, object]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        _require_history_limit(limit)
        with self.engine.begin() as conn:
            source_row_in_tx(self.source_registry_repository, conn, ctx, source_name)
        rows = self.source_registry_repository.list_connection_tests(
            tenant_id=ctx.tenant_id,
            source_name=source_name,
            limit=limit,
        )
        return [egress_attempt_view(row) for row in rows]

    def _start_or_replay(
        self,
        ctx: RequestContext,
        source_name: str,
        expected_fingerprint: str,
        idempotency_key: str,
    ) -> tuple[SourceConnectionRow, SourceConnectionTestRecord, SourceConnectionTestRow | None]:
        with self.engine.begin() as conn:
            source = source_row_in_tx(self.source_registry_repository, conn, ctx, source_name)
            _require_current_fingerprint(source, expected_fingerprint)
            replay = self._replay_row(conn, ctx, source_name, idempotency_key)
            if replay is not None:
                _require_replay_fingerprint(replay, expected_fingerprint)
                return source, _record_from_row(replay), replay
            record = _connection_test_record(ctx, source, idempotency_key)
            replay = self._create_started_record(conn, ctx, source, record)
            return source, record, replay

    def _create_started_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source: SourceConnectionRow,
        record: SourceConnectionTestRecord,
    ) -> SourceConnectionTestRow | None:
        try:
            self.source_registry_repository.create_connection_test(transaction=conn, record=record)
        except SourceConnectionTestAlreadyExistsError:
            replay = self._replay_row(conn, ctx, record.source_name, record.idempotency_key)
            if replay is None:
                raise ConflictDetected("source connection test already exists") from None
            _require_replay_fingerprint(replay, record.config_fingerprint)
            return replay
        self._record_event(conn, ctx, source, record.test_id, "started", record.idempotency_key)
        return None

    def _replay_row(
        self, conn: TransactionContext, ctx: RequestContext, source_name: str, idempotency_key: str
    ) -> SourceConnectionTestRow | None:
        return self.source_registry_repository.connection_test_by_idempotency_key(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=source_name,
            idempotency_key=idempotency_key,
        )

    def _probe_source(self, ctx: RequestContext, source: SourceConnectionRow) -> SourceProbeOutcome:
        route = resolve_source_network_route(self.engine, self.source_management_repository, ctx, source)
        if source["kind"] in {"rest", "rest_api", "sap_odata"}:
            return self._probe_rest(ctx, source, route)
        if source["kind"] == "postgres_jdbc":
            return self._probe_database(ctx, source, route)
        if source["kind"] == "kafka":
            return self._probe_stream(ctx, source, route)
        raise ValidationFailed(
            "source type does not support live connection tests",
            details={"sourceType": source["kind"]},
        )

    def _probe_rest(
        self,
        ctx: RequestContext,
        source: SourceConnectionRow,
        route: ConnectorNetworkRoute,
    ) -> SourceProbeOutcome:
        sync = _first_source_sync(self.source_management_repository, ctx, source["source_name"])
        config = _mapping(sync["config_summary"])
        connector_name = _required_config_text(config, "connectorName")
        resource_name = _required_config_text(config, "resourceName")
        result = self.connector_onboarding_service.test_resource(
            connector_name,
            resource_name,
            ctx=ctx,
            network_route=route,
        )
        if result.get("status") != "succeeded":
            raise _rest_probe_error(result)
        return SourceProbeOutcome(
            details={
                "adapterProfile": "rest-pull-connector",
                "connectorName": connector_name,
                "resourceName": resource_name,
                "rowCount": result.get("rowCount", 0),
                "schema": dict(_mapping(result.get("schema"))),
                "datasetCommitCreated": False,
                "networkEvidence": dict(_mapping(result.get("networkEvidence"))),
            },
            credential_status="succeeded",
            credential_detail="인증정보를 적용한 외부 요청이 성공했습니다.",
        )

    def _probe_database(
        self,
        ctx: RequestContext,
        source: SourceConnectionRow,
        route: ConnectorNetworkRoute,
    ) -> SourceProbeOutcome:
        secret_ref = _required_config_text(source["config_summary"], "databaseUrlSecretRef")
        database_url = self.secret_vault.get_secret(secret_ref).value
        started_at = time.monotonic()
        probe = self.source_database_adapter.test_connection(
            database_url,
            network_route=route,
            connection_id=ctx.request_id,
        )
        network_evidence = dict(probe.network_evidence) or _database_network_evidence(
            route,
            database_url,
            ctx.request_id,
            started_at,
        )
        return SourceProbeOutcome(
            details={
                "adapterProfile": probe.adapter_profile,
                "databaseKind": probe.database_kind,
                "driver": probe.driver,
                "visibleResourceCount": probe.visible_resource_count,
                "datasetCommitCreated": False,
                "networkEvidence": network_evidence,
            },
            credential_status="succeeded",
            credential_detail=f"secretRef {secret_ref}를 사용한 인증이 성공했습니다.",
        )

    def _probe_stream(
        self,
        ctx: RequestContext,
        source: SourceConnectionRow,
        route: ConnectorNetworkRoute,
    ) -> SourceProbeOutcome:
        started_at = time.monotonic()
        connection = source_stream_connection(self, ctx, source["source_name"])
        probe = self.source_stream_adapter.test_connection(connection)
        return SourceProbeOutcome(
            details={
                "adapterProfile": probe.adapter_profile,
                "visibleResourceCount": probe.topic_count,
                "brokerCount": probe.broker_count,
                "topicCount": probe.topic_count,
                "datasetCommitCreated": False,
                "networkEvidence": stream_network_evidence(
                    route,
                    connection.bootstrap_servers,
                    ctx.request_id,
                    max(0, round((time.monotonic() - started_at) * 1000)),
                ),
            },
            credential_status="succeeded",
            credential_detail="Kafka broker metadata 인증 및 조회가 성공했습니다.",
        )

    def _complete(
        self,
        ctx: RequestContext,
        record: SourceConnectionTestRecord,
        source: SourceConnectionRow,
        *,
        outcome: SourceProbeOutcome | None,
        error: FoundryLiteError | None,
    ) -> dict[str, object]:
        status = "succeeded" if outcome is not None else "failed"
        checks = connection_checks(source, outcome, error, request_id=ctx.request_id)
        error_payload = _error_payload(error, ctx.request_id) if error is not None else None
        with self.engine.begin() as conn:
            row = self.source_registry_repository.complete_connection_test(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                test_id=record.test_id,
                status=status,
                checks=checks,
                error=error_payload,
                completed_at=_now(),
            )
            if row is None:
                raise ConflictDetected("source connection test could not be completed")
            self._record_event(conn, ctx, source, record.test_id, status, record.idempotency_key)
        return connection_test_view(row)

    def _record_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source: SourceConnectionRow,
        test_id: str,
        status: str,
        idempotency_key: str,
    ) -> None:
        payload = {"sourceName": source["source_name"], "connectionTestId": test_id, "status": status}
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"source.connection_test.{status}",
            resource_type="source",
            resource_id=source["source_name"],
            action="source_manage",
            after_ref=payload,
            correlation_id=idempotency_key,
        )
        self.runtime_service._outbox(
            conn,
            ctx,
            f"source.connection_test.{status}",
            "source",
            source["source_name"],
            payload,
            idempotency_key=f"{idempotency_key}:{status}",
            correlation_id=idempotency_key,
        )


def connection_test_view(row: Mapping[str, object], *, is_replayed: bool = False) -> dict[str, object]:
    return {
        "connectionTestId": row["id"],
        "sourceName": row["source_name"],
        "sourceType": row["source_type"],
        "status": row["status"],
        "configFingerprint": row["config_fingerprint"],
        "checks": dict(_mapping(row["checks"])),
        "error": dict(_mapping(row["error"])) if isinstance(row.get("error"), Mapping) else None,
        "operationsPath": row["operations_path"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "requestId": _mapping(row["checks"]).get("requestId"),
        "isIdempotentReplay": is_replayed,
    }


def _connection_test_record(
    ctx: RequestContext, source: SourceConnectionRow, idempotency_key: str
) -> SourceConnectionTestRecord:
    test_id = _new_id("source_connection_test")
    started_at = _now()
    return SourceConnectionTestRecord(
        test_id=test_id,
        tenant_id=ctx.tenant_id,
        source_name=source["source_name"],
        source_type=source["kind"],
        status="running",
        config_fingerprint=source["config_fingerprint"],
        idempotency_key=idempotency_key,
        checks={"requestId": ctx.request_id, "items": []},
        error=None,
        operations_path=f"/api/sources/{source['source_name']}/connection-tests",
        started_at=started_at,
        completed_at=None,
        created_at=started_at,
    )


def _first_source_sync(
    repository: SourceManagementRepository, ctx: RequestContext, source_name: str
) -> Mapping[str, object]:
    rows = repository.list_syncs(tenant_id=ctx.tenant_id)
    sync = next((row for row in rows if row["source_name"] == source_name), None)
    if sync is None:
        raise ValidationFailed("REST Source connection test requires one configured Sync")
    return sync


def _rest_probe_error(result: Mapping[str, object]) -> ValidationFailed:
    error = _mapping(result.get("error"))
    message = str(error.get("message") or "REST Source preview failed")
    return ValidationFailed(message, details=dict(_mapping(error.get("details"))))


def _database_network_evidence(
    route: ConnectorNetworkRoute,
    database_url: str,
    connection_id: str,
    started_at: float,
) -> dict[str, object]:
    return _database_route_evidence(
        route,
        database_url,
        connection_id,
        response_flags="NONE",
        is_egress_succeeded=True,
        duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
    )


def _database_route_evidence(
    route: ConnectorNetworkRoute,
    database_url: str,
    connection_id: str,
    *,
    response_flags: str,
    is_egress_succeeded: bool,
    duration_ms: int,
) -> dict[str, object]:
    split = urlsplit(database_url)
    destination_port = _database_port(split.scheme, split.port)
    return {
        "connectionId": f"{connection_id}:database",
        "egressSucceeded": is_egress_succeeded,
        "responseFlags": response_flags,
        "bytesSent": 0,
        "bytesReceived": 0,
        "durationMs": duration_ms,
        "destinationPort": destination_port,
        "origin": "agent-proxy" if route.mode == "agent_proxy" else "connectivity-sidecar",
        "networkType": route.mode,
        "networkResources": {"networkPolicy": route.policy_name, "agentId": route.agent_id},
    }


def _database_port(scheme: str, configured_port: int | None) -> int:
    if configured_port is not None:
        return configured_port
    return 5432 if scheme.startswith("postgres") else 0


def _require_history_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValidationFailed("connection test limit must be between 1 and 100", details={"limit": limit})


def _required_config_text(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValidationFailed("Source connection test configuration is incomplete", details={"field": key})


def _require_current_fingerprint(source: SourceConnectionRow, expected: str) -> None:
    if source["config_fingerprint"] != expected:
        raise ConflictDetected("source config changed before connection test")


def _require_replay_fingerprint(row: SourceConnectionTestRow, expected: str) -> None:
    if row["config_fingerprint"] != expected:
        raise ConflictDetected("connection test idempotency key was used with different Source config")


def _error_payload(error: FoundryLiteError, request_id: str) -> dict[str, object]:
    return {
        "type": error.code,
        "message": scrub_error_text(error.message),
        "details": scrub_error_mapping(error.details),
        "requestId": request_id,
        "retryable": error.code in {"ADAPTER_TIMEOUT", "ADAPTER_UNAVAILABLE", "RATE_LIMITED"},
    }


def _record_from_row(row: SourceConnectionTestRow) -> SourceConnectionTestRecord:
    return SourceConnectionTestRecord(
        test_id=row["id"],
        tenant_id=row["tenant_id"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        status=row["status"],
        config_fingerprint=row["config_fingerprint"],
        idempotency_key=row["idempotency_key"],
        checks=row["checks"],
        error=row["error"],
        operations_path=row["operations_path"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
