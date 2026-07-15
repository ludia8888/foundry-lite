from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.ports import ConnectorNetworkRoute, SourceConnectionTestAlreadyExistsError
from foundry_lite.application.ports.source_stream_adapter import (
    SourceStreamConnection,
    SourceStreamLag,
    SourceStreamSubscription,
    SourceStreamTopic,
)
from foundry_lite.application.services import source_connection_test_service as connection_tests
from foundry_lite.application.services import source_connection_test_views as connection_views
from foundry_lite.application.services import source_management_streaming as streaming
from foundry_lite.application.services import source_network_routing as routing
from foundry_lite.application.services import source_schedule_management as schedules
from foundry_lite.application.services import source_streaming_lifecycle as lifecycle
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class _Transaction:
    def __enter__(self):
        return object()

    def __exit__(self, *_args: object) -> None:
        return None


class _Engine:
    def begin(self) -> _Transaction:
        return _Transaction()


class _RoutingRepository:
    def __init__(self, *, policy=None, agent=None) -> None:
        self.policy = policy
        self.agent = agent

    def network_policy_by_name(self, **_kwargs):
        return self.policy

    def agent_by_id(self, **_kwargs):
        return self.agent


def test_source_network_routes_cover_direct_agent_and_fail_closed_states() -> None:
    ctx = RequestContext(tenant_id="tenant-live")
    direct_source = cast(dict[str, object], {"config_summary": {"connectionMode": "direct"}})
    direct = routing.resolve_source_network_route(_Engine(), _RoutingRepository(), ctx, direct_source)
    assert direct == ConnectorNetworkRoute(mode="direct")

    agent_source = cast(
        dict[str, object],
        {"config_summary": {"connectionMode": "agent_proxy", "agentId": "agent-1"}},
    )
    with pytest.raises(ValidationFailed) as missing_policy:
        routing.resolve_source_network_route(_Engine(), _RoutingRepository(), ctx, agent_source)
    assert missing_policy.value.details["networkEvidence"]["responseFlags"] == "NETWORK_POLICY_REQUIRED"

    policy_source = cast(
        dict[str, object],
        {
            "config_summary": {
                "connectionMode": "agent_proxy",
                "agentId": "agent-1",
                "networkPolicyName": "private-db",
            }
        },
    )
    with pytest.raises(ValidationFailed) as no_policy:
        routing.resolve_source_network_route(_Engine(), _RoutingRepository(), ctx, policy_source)
    assert no_policy.value.details["networkEvidence"]["responseFlags"] == "NETWORK_POLICY_NOT_FOUND"

    inactive = _policy(status="paused")
    with pytest.raises(ValidationFailed) as inactive_policy:
        routing.resolve_source_network_route(_Engine(), _RoutingRepository(policy=inactive), ctx, policy_source)
    assert inactive_policy.value.details["networkEvidence"]["responseFlags"] == "NETWORK_POLICY_INACTIVE"

    mismatched = {**_policy(), "agent_id": "agent-2"}
    with pytest.raises(ValidationFailed) as mismatch:
        routing.resolve_source_network_route(_Engine(), _RoutingRepository(policy=mismatched), ctx, policy_source)
    assert mismatch.value.details["networkEvidence"]["responseFlags"] == "AGENT_MISMATCH"

    offline = _agent(status="offline")
    with pytest.raises(ValidationFailed) as offline_error:
        routing.resolve_source_network_route(
            _Engine(), _RoutingRepository(policy=_policy(), agent=offline), ctx, policy_source
        )
    assert offline_error.value.details["networkEvidence"]["responseFlags"] == "AGENT_OFFLINE"

    missing_proxy = _agent(proxy_url=None)
    with pytest.raises(ValidationFailed) as proxy_error:
        routing.resolve_source_network_route(
            _Engine(), _RoutingRepository(policy=_policy(), agent=missing_proxy), ctx, policy_source
        )
    assert proxy_error.value.details["networkEvidence"]["responseFlags"] == "AGENT_PROXY_URL_MISSING"

    route = routing.resolve_source_network_route(
        _Engine(), _RoutingRepository(policy=_policy(), agent=_agent()), ctx, policy_source
    )
    assert route.mode == "agent_proxy"
    assert route.proxy_url == "http://agent.internal:8765"
    assert route.allowed_destinations == ("db.internal:5432",)
    assert routing._allowed_destinations({"hosts": [" db.internal ", 7, ""]}) == ("db.internal",)
    assert routing._is_agent_fresh({"status": "online", "last_heartbeat_at": "not-a-time"}) is False


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "incomplete"),
        ({"partitionMode": "random"}, "partitionMode is invalid"),
        ({"partitions": []}, "non-empty integer list"),
        ({"partitions": [0, -1]}, "non-negative integers"),
        ({"monitoring": "bad"}, "must be an object"),
        ({"monitoring": {"maxBrokerLag": True}}, "threshold is invalid"),
    ],
)
def test_streaming_config_additional_contract_edges(config: dict[str, object], message: str) -> None:
    from foundry_lite.application.services.source_streaming_config import validate_streaming_sync_config

    baseline: dict[str, object] = {
        "bootstrapServers": "broker:9092",
        "topic": "trades",
        "streamName": "trades",
        "consumerGroup": "foundry",
    }
    if not config:
        baseline = {}
    else:
        baseline.update(config)
    with pytest.raises(ValidationFailed, match=message):
        validate_streaming_sync_config("kafka", "streaming", baseline)
    validate_streaming_sync_config("rest_api", "snapshot", {})


def test_streaming_lifecycle_status_handles_idle_terminal_and_stale_workers() -> None:
    sync = _streaming_sync()
    observed = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    idle = lifecycle.streaming_sync_status_view(sync, None, observed_at=observed)
    assert idle["status"] == "idle"
    assert idle["lifecycleState"] == "IDLE"

    cancelled = lifecycle.streaming_sync_status_view(
        sync,
        _workflow_row("cancelled", output={"lifecycleState": "STOP_REQUESTED"}),
        observed_at=observed,
    )
    assert cancelled["lifecycleState"] == "STOP_REQUESTED"
    failed = lifecycle.streaming_sync_status_view(sync, _workflow_row("failed"), observed_at=observed)
    assert failed["lifecycleState"] == "FAILED"

    stale = lifecycle.streaming_sync_status_view(
        sync,
        _workflow_row(
            "running",
            output={"workerLease": {"leaseExpiresAt": (observed - timedelta(seconds=1)).isoformat()}},
        ),
        observed_at=observed,
    )
    assert stale["isWorkerStale"] is True
    assert stale["lifecycleState"] == "UNHEALTHY"

    malformed = lifecycle.streaming_sync_status_view(
        sync,
        _workflow_row("running", output={"workerLease": {"leaseExpiresAt": "bad"}}),
        observed_at=observed,
    )
    assert malformed["isWorkerStale"] is True
    assert lifecycle._assignment_grace_expired(None, observed) is False
    assert lifecycle._assignment_grace_expired("bad", observed) is True
    with pytest.raises(ValidationFailed, match="only available"):
        lifecycle._require_streaming_kafka_sync({"sync_name": "rest", "source_type": "rest_api"})


def test_streaming_lifecycle_missing_rows_and_link_updates_fail_closed() -> None:
    ctx = RequestContext(tenant_id="tenant-live")
    runtime = SimpleNamespace(workflow_run_by_id=lambda **_kwargs: None)
    dependencies = SimpleNamespace(
        runtime_repository=runtime,
        source_management_repository=SimpleNamespace(
            update_sync_streaming_workflow=lambda **_kwargs: None,
        ),
    )
    with pytest.raises(NotFound, match="workflow not found"):
        lifecycle._workflow_row(dependencies, object(), ctx, "workflow-missing")
    with pytest.raises(NotFound, match="sync not found"):
        lifecycle._link_sync_workflow(
            dependencies,
            object(),
            ctx,
            _streaming_sync(),
            _workflow_row("requested"),
        )


def test_streaming_lifecycle_additional_health_and_stop_boundaries() -> None:
    observed = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    assert lifecycle._health_lifecycle("RUNNING", {"status": "UNHEALTHY"}) == "UNHEALTHY"
    assert lifecycle._health_lifecycle("RUNNING", {"status": "DEGRADED"}) == "DEGRADED"
    assert lifecycle._health_lifecycle("STARTING", {"status": "UNHEALTHY"}) == "STARTING"
    assert lifecycle._is_worker_stale("requested", {}, observed, observed.isoformat()) is False
    assert lifecycle._is_worker_stale("running", {"leaseExpiresAt": 7}, observed, None) is True
    assert lifecycle._is_worker_stale("requested", {"leaseExpiresAt": 7}, observed, None) is False


def test_source_schedule_transition_helpers_cover_concurrency_and_validation() -> None:
    recurring = {**_streaming_sync(), "status": "paused", "schedule": {"mode": "interval", "everySeconds": 60}}
    resumed = schedules._transitioned_schedule(recurring, "active")
    assert resumed["mode"] == "interval"
    assert schedules._schedule_state_transition("paused").to_status == "paused"
    assert schedules._schedule_state_transition("active").to_status == "active"
    with pytest.raises(ValidationFailed, match="unsupported"):
        schedules._transitioned_schedule({**recurring, "status": "failed"}, "active")
    with pytest.raises(ValidationFailed, match="recurring"):
        schedules._transitioned_schedule({**recurring, "schedule": {"mode": "manual"}}, "active")

    ctx = RequestContext(tenant_id="tenant-live")
    repository = SimpleNamespace(sync_by_name=lambda **_kwargs: recurring)
    service = SimpleNamespace(source_management_repository=repository)
    assert schedules._concurrent_state_result(service, object(), ctx, "live_sync", "paused") == recurring
    with pytest.raises(ConflictDetected, match="changed while"):
        schedules._concurrent_state_result(service, object(), ctx, "live_sync", "active")
    with pytest.raises(NotFound, match="sync not found"):
        schedules._audited_update(SimpleNamespace(), object(), ctx, "live_sync", "idem", recurring, None, "updated")


def test_streaming_management_helpers_validate_partitions_credentials_lag_and_checkpoint() -> None:
    ctx = RequestContext(tenant_id="tenant-live")
    missing_source = SimpleNamespace(
        engine=_Engine(),
        source_registry_repository=SimpleNamespace(source_by_name=lambda **_kwargs: None),
        source_management_repository=SimpleNamespace(),
        secret_vault=SimpleNamespace(),
    )
    with pytest.raises(NotFound, match="source not found"):
        streaming.source_stream_connection(missing_source, ctx, "missing")

    service = SimpleNamespace(
        source_management_repository=SimpleNamespace(credential_by_name=lambda **_kwargs: None),
        secret_vault=SimpleNamespace(),
    )
    assert streaming._source_credential(service, object(), ctx, {}) is None
    with pytest.raises(NotFound, match="credential not found"):
        streaming._source_credential(service, object(), ctx, {"credentialName": "kafka-secret"})

    connection = SourceStreamConnection(bootstrap_servers="broker:9092")
    no_topic = SimpleNamespace(
        source_stream_adapter=SimpleNamespace(
            list_topics=lambda *_args, **_kwargs: (SourceStreamTopic("other", 1, False),)
        )
    )
    with pytest.raises(NotFound, match="topic not found"):
        streaming._stream_partitions(no_topic, connection, {"partitionMode": "all", "topic": "trades"})
    zero_partitions = SimpleNamespace(
        source_stream_adapter=SimpleNamespace(
            list_topics=lambda *_args, **_kwargs: (SourceStreamTopic("trades", 0, False),)
        )
    )
    with pytest.raises(ValidationFailed, match="no readable partitions"):
        streaming._stream_partitions(zero_partitions, connection, {"partitionMode": "all", "topic": "trades"})
    assert streaming._validated_partitions([2, 0]) == (0, 2)
    with pytest.raises(ValidationFailed, match="invalid Kafka partition"):
        streaming._validated_partitions([0, True])
    with pytest.raises(ValidationFailed, match="duplicates"):
        streaming._validated_partitions([1, 1])
    assert streaming._partition_checkpoint_start({"checkpoint_start": {"partitions": {"2": 8}}}, 2) == {"offset": 8}
    assert streaming._partition_checkpoint_start({"checkpoint_start": {"partition": 1, "offset": 8}}, 2) == {}
    assert streaming._total_lag([2, 3]) == 5
    assert streaming._total_lag([2, True]) is None
    assert streaming._total_lag([]) is None
    assert streaming._float_value(True) == 0.0
    assert streaming._float_value(2.5) == 2.5

    lag_error_service = SimpleNamespace(
        source_stream_adapter=SimpleNamespace(
            read_lag=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValidationFailed("lag unavailable"))
        )
    )
    observation = streaming._read_lag(
        lag_error_service,
        connection,
        SourceStreamSubscription("trades", "trades", "foundry", 0),
        [],
        {"offset": 5},
    )
    assert observation.lag is None
    assert observation.error["code"] == "VALIDATION_FAILED"

    lag_service = SimpleNamespace(
        source_stream_adapter=SimpleNamespace(
            read_lag=lambda *_args, **kwargs: SourceStreamLag(0, 8, kwargs["current_offset"], 2)
        )
    )
    measured = streaming._read_lag(
        lag_service,
        connection,
        SourceStreamSubscription("trades", "trades", "foundry", 0),
        [],
        {"offset": 5},
    )
    assert measured.lag.current_offset == 5


def test_source_connection_test_helpers_cover_missing_config_and_evidence_paths() -> None:
    ctx = RequestContext(tenant_id="tenant-live", request_id="req-live")
    with pytest.raises(ValidationFailed, match="requires one configured Sync"):
        connection_tests._first_source_sync(SimpleNamespace(list_syncs=lambda **_kwargs: []), ctx, "rest")
    assert connection_tests._database_port("postgresql", None) == 5432
    assert connection_tests._database_port("mysql", 3306) == 3306
    with pytest.raises(ValidationFailed, match="between 1 and 100"):
        connection_tests._require_history_limit(0)
    assert connection_tests._required_config_text({"topic": " trades "}, "topic") == "trades"
    with pytest.raises(ValidationFailed, match="configuration is incomplete"):
        connection_tests._required_config_text({}, "topic")
    with pytest.raises(ConflictDetected, match="config changed"):
        connection_tests._require_current_fingerprint(cast(object, {"config_fingerprint": "sha256:old"}), "sha256:new")
    with pytest.raises(ConflictDetected, match="different Source config"):
        connection_tests._require_replay_fingerprint(cast(object, {"config_fingerprint": "sha256:old"}), "sha256:new")

    service = object.__new__(connection_tests.SourceConnectionTestService)
    service.engine = _Engine()
    service.source_management_repository = _RoutingRepository()
    with pytest.raises(ValidationFailed, match="does not support"):
        service._probe_source(
            ctx,
            cast(
                object,
                {"kind": "filesystem", "source_name": "files", "config_summary": {"connectionMode": "direct"}},
            ),
        )

    error = ValidationFailed(
        "unauthorized",
        details={
            "networkStage": "http",
            "networkEvidence": {"egressSucceeded": True, "networkType": "agent_proxy"},
        },
    )
    checks = connection_views.connection_checks(
        cast(object, {"kind": "rest_api"}),
        None,
        error,
        request_id="req-live",
    )
    assert checks["summary"]["passed"] == 3
    assert next(item for item in checks["items"] if item["key"] == "credential")["status"] == "failed"
    assert "Agent CONNECT" in next(item for item in checks["items"] if item["key"] == "network_route")["detail"]
    assert connection_views.network_evidence(None, None) == {}

    row = {
        "id": "test-1",
        "source_name": "rest",
        "checks": {"requestId": "req-live", "probe": {}},
        "error": {"details": {"networkEvidence": {"responseFlags": "TLS_ERROR", "durationMs": "bad"}}},
        "started_at": "start",
        "completed_at": "end",
    }
    egress = connection_views.egress_attempt_view(row)
    assert egress["responseFlags"] == "TLS_ERROR"
    assert egress["durationMs"] == 0


def test_source_connection_test_concurrent_idempotency_race_is_replayed_or_conflicts() -> None:
    ctx = RequestContext(tenant_id="tenant-live", request_id="req-live")
    source = cast(
        object,
        {
            "source_name": "rest",
            "kind": "rest_api",
            "config_fingerprint": "sha256:rest",
        },
    )
    record = connection_tests._connection_test_record(ctx, source, "idem-live")

    class _Repository:
        def __init__(self, replay) -> None:
            self.replay = replay

        def create_connection_test(self, **_kwargs) -> None:
            raise SourceConnectionTestAlreadyExistsError()

        def connection_test_by_idempotency_key(self, **_kwargs):
            return self.replay

    service = object.__new__(connection_tests.SourceConnectionTestService)
    service.source_registry_repository = _Repository(None)
    with pytest.raises(ConflictDetected, match="already exists"):
        service._create_started_record(object(), ctx, source, record)

    replay = {
        "id": record.test_id,
        "tenant_id": ctx.tenant_id,
        "source_name": "rest",
        "source_type": "rest_api",
        "status": "running",
        "config_fingerprint": "sha256:rest",
        "idempotency_key": "idem-live",
        "checks": {},
        "error": None,
        "operations_path": "/api/sources/rest/connection-tests",
        "started_at": "start",
        "completed_at": None,
        "created_at": "start",
    }
    service.source_registry_repository = _Repository(replay)
    assert service._create_started_record(object(), ctx, source, record) == replay


def _policy(*, status: str = "active") -> dict[str, object]:
    return {
        "status": status,
        "mode": "agent_proxy",
        "agent_id": "agent-1",
        "allowed_hosts": {"hosts": ["db.internal:5432"]},
    }


def _agent(*, status: str = "online", proxy_url: str | None = "http://agent.internal:8765") -> dict[str, object]:
    return {
        "status": status,
        "last_heartbeat_at": datetime.now(UTC).isoformat(),
        "network_summary": {"proxyUrl": proxy_url} if proxy_url is not None else {},
    }


def _streaming_sync() -> dict[str, object]:
    return {
        "sync_name": "live_sync",
        "source_name": "kafka_source",
        "source_type": "kafka",
        "capability": "streaming",
        "config_fingerprint": "sha256:live",
        "target_dataset_ref": "live.trades",
    }


def _workflow_row(status: str, *, output: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": "workflow-live",
        "tenant_id": "tenant-live",
        "workflow_name": "SourceStreamingSync",
        "workflow_profile": "source-streaming-v1",
        "status": status,
        "idempotency_key": "idem-live",
        "request_fingerprint": "sha256:request",
        "input": {},
        "output": output or {},
        "error": {"code": "FAILED"} if status == "failed" else None,
        "dataset_id": None,
        "audit_event_id": None,
        "attempts": 1,
        "created_at": "2026-07-16T00:00:00Z",
        "started_at": None,
        "completed_at": None,
    }
