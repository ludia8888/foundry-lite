from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import sleep
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import AdapterFailureContract, StreamAdapter, StreamPublishRequest
from foundry_lite.application.ports.source_stream_adapter import (
    SourceStreamConnection,
    SourceStreamConnectionProbe,
    SourceStreamLag,
    SourceStreamSubscription,
    SourceStreamTopic,
)
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure.adapters import LocalStreamAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.source_streaming import SourceStreamingServiceConfig, run_source_streaming_service


class _SourceStreamAdapter:
    profile_name = "test-source-stream"

    def __init__(self, reader: StreamAdapter) -> None:
        self.reader = reader
        self.opened: list[SourceStreamSubscription] = []

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def test_connection(self, connection: SourceStreamConnection) -> SourceStreamConnectionProbe:
        return SourceStreamConnectionProbe(adapter_profile=self.profile_name, broker_count=1, topic_count=1)

    def list_topics(self, connection: SourceStreamConnection, *, limit: int) -> tuple[SourceStreamTopic, ...]:
        assert connection.bootstrap_servers == "redpanda:9092"
        assert limit == 10
        return (SourceStreamTopic(topic_name="crypto.trades", partition_count=1, is_internal=False),)

    def open_stream(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
    ) -> StreamAdapter:
        assert connection.bootstrap_servers == "redpanda:9092"
        self.opened.append(subscription)
        return self.reader

    def read_lag(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
        *,
        current_offset: int | None,
    ) -> SourceStreamLag:
        return SourceStreamLag(earliest_offset=0, end_offset=2, current_offset=current_offset, lag=0)


class _PartitionedSourceStreamAdapter(_SourceStreamAdapter):
    def __init__(self, readers: dict[int, StreamAdapter]) -> None:
        super().__init__(readers[0])
        self.readers = readers

    def list_topics(self, connection: SourceStreamConnection, *, limit: int) -> tuple[SourceStreamTopic, ...]:
        return (SourceStreamTopic(topic_name="crypto.trades", partition_count=len(self.readers), is_internal=False),)

    def open_stream(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
    ) -> StreamAdapter:
        self.opened.append(subscription)
        return self.readers[subscription.partition]

    def read_lag(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
        *,
        current_offset: int | None,
    ) -> SourceStreamLag:
        end_offset = 2 if subscription.partition == 0 else 1
        current = current_offset if current_offset is not None else -1
        return SourceStreamLag(
            earliest_offset=0,
            end_offset=end_offset,
            current_offset=current_offset,
            lag=max(0, end_offset - current - 1),
        )


def test_kafka_source_explores_topic_then_commits_checkpointed_streaming_sync(tmp_path: Path) -> None:
    reader = LocalStreamAdapter()
    _publish_trade(reader, offset_key="trade-1", price=101_250.5)
    _publish_trade(reader, offset_key="trade-2", price=101_251.0)
    source_adapter = _SourceStreamAdapter(reader)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            source=replace(
                dependencies.source,
                source_stream_adapter=source_adapter,
            ),
        )
    )
    ctx = demo_admin_context()

    sync = foundry.sources.create_managed_sync(
        sync_name="crypto_trades_live",
        source_name="local_redpanda",
        display_name="Local Redpanda",
        source_type="kafka",
        capability="streaming",
        mode="APPEND",
        target_dataset_ref="live.crypto_trades",
        schedule={"mode": "manual"},
        config_summary={
            "bootstrapServers": "redpanda:9092",
            "connectionMode": "direct",
            "topic": "crypto.trades",
            "partition": 0,
            "streamName": "crypto-trades",
            "consumerGroup": "foundry-live-crypto",
            "batchLimit": 100,
        },
        idempotency_key="sync-local-redpanda",
        ctx=ctx,
    )
    source = foundry.sources.get_source("local_redpanda", ctx=ctx)
    connection_test = foundry.sources.test_connection(
        "local_redpanda",
        expected_config_fingerprint=cast(str, source["configFingerprint"]),
        idempotency_key="test-local-redpanda",
        ctx=ctx,
    )
    topics = foundry.sources.explore_source(
        source_name="local_redpanda",
        source_type="kafka",
        request={"sampleLimit": 10},
        ctx=ctx,
    )
    preview = foundry.sources.explore_source(
        source_name="local_redpanda",
        source_type="kafka",
        request={"topic": "crypto.trades", "streamName": "crypto-trades", "sampleLimit": 1},
        ctx=ctx,
    )

    run = foundry.sources.start_managed_sync_run(
        "crypto_trades_live",
        idempotency_key="run-local-redpanda-1",
        ctx=ctx,
    )

    topic_result = cast(dict[str, object], topics["resultSummary"])
    preview_result = cast(dict[str, object], preview["resultSummary"])
    result = cast(dict[str, object], run["resultSummary"])
    assert sync["sourceType"] == "kafka"
    assert source["kind"] == "kafka"
    assert connection_test["status"] == "succeeded"
    assert cast(dict[str, object], connection_test["checks"])["summary"] == {"passed": 5, "total": 5}
    assert cast(list[dict[str, object]], topic_result["topics"])[0]["topicName"] == "crypto.trades"
    assert cast(list[dict[str, object]], preview_result["sampleRows"])[0]["key"] == "trade-1"
    assert preview_result["datasetCommitCreated"] is False
    assert run["status"] == "succeeded"
    assert run["checkpointEnd"] == {
        "topic": "crypto.trades",
        "partition": 0,
        "consumerGroup": "foundry-live-crypto",
        "offset": 1,
    }
    assert result["status"] == "committed"
    assert result["eventCount"] == 2
    assert result["brokerEndOffset"] == 2
    assert result["brokerLag"] == 0
    assert len(foundry.datasets.preview("live.crypto_trades", ctx=ctx)) == 2

    _publish_trade(reader, offset_key="trade-3", price=101_252.0)
    incremental_run = foundry.sources.start_managed_sync_run(
        "crypto_trades_live",
        idempotency_key="run-local-redpanda-2",
        ctx=ctx,
    )
    current_view = foundry.datasets.preview("live.crypto_trades", ctx=ctx)
    inspection = foundry.datasets.inspect("live.crypto_trades", ctx=ctx)

    assert incremental_run["checkpointStart"] == run["checkpointEnd"]
    assert incremental_run["checkpointEnd"]["offset"] == 2
    assert cast(dict[str, object], incremental_run["resultSummary"])["eventCount"] == 1
    assert [row["event_key"] for row in current_view] == ["trade-1", "trade-2", "trade-3"]
    assert inspection["version"]["row_count"] == 3
    assert len(inspection["manifest"]["files"]) == 2


def test_streaming_lifecycle_runs_idle_checkpoints_without_losing_offset(tmp_path: Path) -> None:
    reader = LocalStreamAdapter()
    _publish_trade(reader, offset_key="trade-live-1", price=101_300.0)
    foundry = _streaming_foundry(tmp_path, reader)
    ctx = demo_admin_context()
    sync = _create_streaming_sync(foundry, ctx)

    requested = foundry.sources.start_managed_streaming_sync(
        "crypto_trades_live",
        expected_config_fingerprint=cast(str, sync["configFingerprint"]),
        idempotency_key="stream-start-1",
        ctx=ctx,
    )
    replay = foundry.sources.start_managed_streaming_sync(
        "crypto_trades_live",
        expected_config_fingerprint=cast(str, sync["configFingerprint"]),
        idempotency_key="stream-start-2",
        ctx=ctx,
    )
    result = run_source_streaming_service(
        SourceStreamingServiceConfig(
            sync_name="crypto_trades_live",
            storage_root=tmp_path / "unused",
            max_iterations=2,
            worker_id="worker-live-test",
        ),
        foundry=foundry,
    )
    status = foundry.sources.get_managed_streaming_sync_status("crypto_trades_live", ctx=ctx)
    runs = foundry.sources.list_managed_sync_runs("crypto_trades_live", ctx=ctx)
    stopped = foundry.sources.stop_managed_streaming_sync(
        "crypto_trades_live",
        expected_config_fingerprint=cast(str, sync["configFingerprint"]),
        idempotency_key="stream-stop-1",
        ctx=ctx,
    )

    assert requested["status"] == "requested"
    assert requested["lifecycleState"] == "STARTING"
    assert replay["workflowRunId"] == requested["workflowRunId"]
    assert result.iterations == 2
    assert result.archived_batches == 1
    assert result.rows_archived == 1
    assert status["status"] == "running"
    assert status["lifecycleState"] == "RUNNING"
    assert status["isWorkerStale"] is False
    telemetry = cast(dict[str, object], status["telemetry"])
    assert telemetry["iterations"] == 2
    assert telemetry["archivedBatches"] == 1
    assert cast(dict[str, object], telemetry["checkpoint"])["offset"] == 0
    assert cast(dict[str, object], telemetry["kafka"])["currentOffset"] == 0
    assert runs[0]["checkpointEnd"]["offset"] == 0
    assert runs[1]["checkpointEnd"]["offset"] == 0
    assert len(foundry.datasets.preview("live.crypto_trades", ctx=ctx)) == 1
    assert stopped["status"] == "cancelled"
    assert stopped["desiredState"] == "STOPPED"
    assert stopped["lifecycleState"] == "STOP_REQUESTED"


def test_kafka_streaming_sync_discovers_all_partitions_and_resumes_each_cursor(tmp_path: Path) -> None:
    partition_zero = LocalStreamAdapter()
    partition_one = LocalStreamAdapter()
    _publish_trade(partition_zero, offset_key="p0-trade-1", price=101_400.0)
    _publish_trade(partition_zero, offset_key="p0-trade-2", price=101_401.0)
    _publish_trade(partition_one, offset_key="p1-trade-1", price=101_500.0)
    adapter = _PartitionedSourceStreamAdapter({0: partition_zero, 1: partition_one})
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "multi-partition")
    foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            source=replace(dependencies.source, source_stream_adapter=adapter),
        )
    )
    ctx = demo_admin_context()
    foundry.sources.create_managed_sync(
        sync_name="crypto_all_partitions",
        source_name="local_redpanda",
        display_name="All crypto partitions",
        source_type="kafka",
        capability="streaming",
        mode="APPEND",
        target_dataset_ref="live.crypto_all_partitions",
        schedule={"mode": "manual"},
        config_summary={
            "bootstrapServers": "redpanda:9092",
            "topic": "crypto.trades",
            "partitionMode": "all",
            "streamName": "crypto-trades",
            "consumerGroup": "foundry-live-crypto-all",
            "batchLimit": 100,
        },
        idempotency_key="sync-all-partitions",
        ctx=ctx,
    )

    first = foundry.sources.start_managed_sync_run(
        "crypto_all_partitions", idempotency_key="run-all-partitions-1", ctx=ctx
    )
    _publish_trade(partition_zero, offset_key="p0-trade-3", price=101_402.0)
    second = foundry.sources.start_managed_sync_run(
        "crypto_all_partitions", idempotency_key="run-all-partitions-2", ctx=ctx
    )

    first_checkpoint = cast(dict[str, object], first["checkpointEnd"])
    second_checkpoint = cast(dict[str, object], second["checkpointEnd"])
    first_partitions = cast(dict[str, dict[str, object]], first_checkpoint["partitions"])
    second_partitions = cast(dict[str, dict[str, object]], second_checkpoint["partitions"])
    first_summary = cast(dict[str, object], first["resultSummary"])
    second_summary = cast(dict[str, object], second["resultSummary"])
    rows = foundry.datasets.preview("live.crypto_all_partitions", ctx=ctx)

    assert first_summary["partitionCount"] == 2
    assert first_summary["eventCount"] == 3
    assert first_summary["brokerLag"] == 0
    assert first_partitions["0"]["offset"] == 1
    assert first_partitions["1"]["offset"] == 0
    assert second_summary["eventCount"] == 1
    assert second_partitions["0"]["offset"] == 2
    assert second_partitions["1"]["offset"] == 0
    assert len(rows) == 4
    assert {row["event_id"] for row in rows} == {
        "crypto.trades:0:0",
        "crypto.trades:0:1",
        "crypto.trades:0:2",
        "crypto.trades:1:0",
    }


def test_streaming_standby_worker_takes_over_only_after_active_lease_expires(tmp_path: Path) -> None:
    reader = LocalStreamAdapter()
    _publish_trade(reader, offset_key="takeover-trade-1", price=101_600.0)
    foundry = _streaming_foundry(tmp_path, reader)
    ctx = demo_admin_context()
    sync = _create_streaming_sync(foundry, ctx)
    foundry.sources.start_managed_streaming_sync(
        "crypto_trades_live",
        expected_config_fingerprint=cast(str, sync["configFingerprint"]),
        idempotency_key="takeover-start",
        ctx=ctx,
    )
    worker_a = SourceStreamingServiceConfig(
        sync_name="crypto_trades_live",
        storage_root=tmp_path / "unused-a",
        worker_id="stream-worker-a",
        lease_ttl_seconds=1,
        max_iterations=0,
    )
    worker_b = SourceStreamingServiceConfig(
        sync_name="crypto_trades_live",
        storage_root=tmp_path / "unused-b",
        worker_id="stream-worker-b",
        lease_ttl_seconds=1,
        max_iterations=1,
    )

    first = run_source_streaming_service(worker_a, foundry=foundry)
    with pytest.raises(ConflictDetected, match="owned by another worker"):
        run_source_streaming_service(worker_b, foundry=foundry)
    sleep(1.05)
    takeover = run_source_streaming_service(worker_b, foundry=foundry)
    status = foundry.sources.get_managed_streaming_sync_status("crypto_trades_live", ctx=ctx)

    assert first.stop_reason == "max_iterations"
    assert takeover.rows_archived == 1
    assert status["workerLease"]["ownerId"] == "stream-worker-b"
    assert status["isWorkerStale"] is False


def test_only_one_streaming_sync_can_own_a_dataset_branch(tmp_path: Path) -> None:
    reader = LocalStreamAdapter()
    foundry = _streaming_foundry(tmp_path, reader)
    ctx = demo_admin_context()
    first = _create_streaming_sync(foundry, ctx)
    second = foundry.sources.create_managed_sync(
        sync_name="crypto_trades_shadow",
        source_name="local_redpanda",
        display_name="Shadow crypto trades",
        source_type="kafka",
        capability="streaming",
        mode="APPEND",
        target_dataset_ref="live.crypto_trades",
        schedule={"mode": "manual"},
        config_summary={
            "bootstrapServers": "redpanda:9092",
            "topic": "crypto.trades",
            "partition": 0,
            "streamName": "crypto-trades",
            "consumerGroup": "foundry-live-crypto-shadow",
            "batchLimit": 100,
        },
        idempotency_key="sync-shadow-lifecycle",
        ctx=ctx,
    )
    foundry.sources.start_managed_streaming_sync(
        "crypto_trades_live",
        expected_config_fingerprint=cast(str, first["configFingerprint"]),
        idempotency_key="exclusive-first-start",
        ctx=ctx,
    )

    with pytest.raises(ConflictDetected, match="already active on the target dataset branch"):
        foundry.sources.start_managed_streaming_sync(
            "crypto_trades_shadow",
            expected_config_fingerprint=cast(str, second["configFingerprint"]),
            idempotency_key="exclusive-shadow-start",
            ctx=ctx,
        )


def _streaming_foundry(tmp_path: Path, reader: LocalStreamAdapter) -> FoundryLite:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite-lifecycle")
    return FoundryLite(
        dependencies=replace(
            dependencies,
            source=replace(
                dependencies.source,
                source_stream_adapter=_SourceStreamAdapter(reader),
            ),
        )
    )


def _create_streaming_sync(foundry: FoundryLite, ctx: object) -> dict[str, object]:
    return foundry.sources.create_managed_sync(
        sync_name="crypto_trades_live",
        source_name="local_redpanda",
        display_name="Live crypto trades",
        source_type="kafka",
        capability="streaming",
        mode="APPEND",
        target_dataset_ref="live.crypto_trades",
        schedule={"mode": "manual"},
        config_summary={
            "bootstrapServers": "redpanda:9092",
            "connectionMode": "direct",
            "topic": "crypto.trades",
            "partition": 0,
            "streamName": "crypto-trades",
            "consumerGroup": "foundry-live-crypto",
            "batchLimit": 100,
        },
        idempotency_key="sync-live-lifecycle",
        ctx=ctx,
    )


def _publish_trade(reader: LocalStreamAdapter, *, offset_key: str, price: float) -> None:
    reader.publish_event(
        StreamPublishRequest(
            stream_name="crypto-trades",
            event_type="trade.executed",
            tenant_id="tenant-demo",
            request_id=f"req-{offset_key}",
            key=offset_key,
            payload={"symbol": "BTC/USD", "price": price},
        )
    )
