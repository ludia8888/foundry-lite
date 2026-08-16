"""Kafka Source exploration and managed streaming Sync helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from foundry_lite.application.ports import (
    SourceManagementRepository,
    SourceRegistryRepository,
    StreamAdapter,
    StreamArchiveConfig,
    StreamEvent,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.secret_provider import SecretValue, SecretVault
from foundry_lite.application.ports.source_stream_adapter import (
    SourceStreamAdapter,
    SourceStreamConnection,
    SourceStreamLag,
    SourceStreamSubscription,
    SourceStreamTopic,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.application.services.source_management_helpers import (
    int_value,
    mapping,
    optional_text,
    required_text,
    target_dataset_ref,
)
from foundry_lite.application.services.source_network_routing import resolve_source_network_route
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, ValidationFailed


class StreamingDatasetBoundary(Protocol):
    def archive_stream_events(
        self,
        dataset_ref: str,
        *,
        stream: StreamArchiveConfig,
        ctx: RequestContext | None = None,
        after_offset: int | None = None,
        sync_name: str | None = None,
        pre_commit_check: Callable[[Sequence[StreamEvent]], None] | None = None,
        stream_adapter: StreamAdapter | None = None,
    ) -> CommitResult | None: ...


class StreamingSourceStore(Protocol):
    @property
    def engine(self) -> TransactionManager: ...

    @property
    def source_management_repository(self) -> SourceManagementRepository: ...

    @property
    def source_registry_repository(self) -> SourceRegistryRepository: ...

    @property
    def source_stream_adapter(self) -> SourceStreamAdapter: ...

    @property
    def secret_vault(self) -> SecretVault: ...


@dataclass(frozen=True)
class StreamRunCompletion:
    dataset_version_id: str | None
    checkpoint: Mapping[str, object]
    result: Mapping[str, object]


@dataclass(frozen=True)
class StreamLagObservation:
    lag: SourceStreamLag | None
    error: Mapping[str, object] | None = None


@dataclass(frozen=True)
class StreamPartitionCompletion:
    partition: int
    dataset_version_id: str | None
    checkpoint: Mapping[str, object]
    result: Mapping[str, object]


def explore_stream_source(
    service: StreamingSourceStore,
    ctx: RequestContext,
    source_name: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    connection = source_stream_connection(service, ctx, source_name)
    topic = optional_text(request.get("topic"))
    if topic is None:
        topics = service.source_stream_adapter.list_topics(
            connection,
            limit=int_value(request.get("sampleLimit"), 100),
        )
        return {"topics": [_topic_payload(item) for item in topics], "datasetCommitCreated": False}
    subscription = _subscription(request, source_name, topic)
    reader = service.source_stream_adapter.open_stream(connection, subscription)
    events = reader.read_events(
        subscription.stream_name,
        after_offset=_optional_int(request.get("afterOffset")),
        limit=int_value(request.get("sampleLimit"), 20),
    )
    return _preview_payload(subscription, events)


def complete_stream_run(
    service: StreamingSourceStore,
    dataset_service: StreamingDatasetBoundary,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
) -> StreamRunCompletion:
    summary = mapping(sync["config_summary"])
    connection = source_stream_connection(service, ctx, str(sync["source_name"]))
    partitions = _stream_partitions(service, connection, summary)
    completions = [
        _complete_stream_partition(service, dataset_service, ctx, sync, run, connection, summary, partition)
        for partition in partitions
    ]
    return _combined_stream_completion(summary, completions)


def _complete_stream_partition(
    service: StreamingSourceStore,
    dataset_service: StreamingDatasetBoundary,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
    connection: SourceStreamConnection,
    summary: Mapping[str, object],
    partition: int,
) -> StreamPartitionCompletion:
    stream = _stream_config(summary, run, partition)
    subscription = _subscription_from_config(stream)
    reader = service.source_stream_adapter.open_stream(connection, subscription)
    observed_events: list[StreamEvent] = []
    started = monotonic()
    commit = dataset_service.archive_stream_events(
        target_dataset_ref(sync),
        stream=stream,
        ctx=ctx,
        sync_name=str(sync["sync_name"]),
        pre_commit_check=observed_events.extend,
        stream_adapter=reader,
    )
    checkpoint_start = _partition_checkpoint_start(run, partition)
    lag_observation = _read_lag(service, connection, subscription, observed_events, checkpoint_start)
    completion = _stream_completion(stream, commit, observed_events, lag_observation, started, checkpoint_start)
    return StreamPartitionCompletion(partition, completion.dataset_version_id, completion.checkpoint, completion.result)


def source_stream_connection(
    service: StreamingSourceStore,
    ctx: RequestContext,
    source_name: str,
) -> SourceStreamConnection:
    with service.engine.begin() as conn:
        source = service.source_registry_repository.source_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=source_name,
        )
        if source is None:
            raise NotFound("source not found", details={"source_name": source_name})
        summary = mapping(source["config_summary"])
        credential = _source_credential(service, conn, ctx, summary)
    return SourceStreamConnection(
        bootstrap_servers=required_text(summary, "bootstrapServers"),
        auth_scheme=str(credential[0]) if credential is not None else "none",
        is_tls_enabled=summary.get("isTlsEnabled") is True,
        credential=credential[1] if credential is not None else None,
        network_route=resolve_source_network_route(
            service.engine,
            service.source_management_repository,
            ctx,
            source,
        ),
    )


def _source_credential(
    service: StreamingSourceStore,
    conn: TransactionContext,
    ctx: RequestContext,
    summary: Mapping[str, object],
) -> tuple[str, SecretValue] | None:
    credential_name = optional_text(summary.get("credentialName"))
    if credential_name is None:
        return None
    row = service.source_management_repository.credential_by_name(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        credential_name=credential_name,
    )
    if row is None:
        raise NotFound("source credential not found", details={"credential_name": credential_name})
    return str(row["auth_scheme"]), service.secret_vault.get_secret(str(row["secret_name"]))


def _stream_config(summary: Mapping[str, object], run: Mapping[str, object], partition: int) -> StreamArchiveConfig:
    return StreamArchiveConfig(
        stream_name=required_text(summary, "streamName"),
        topic=required_text(summary, "topic"),
        consumer_group=required_text(summary, "consumerGroup"),
        partition=partition,
        limit=int_value(run.get("batch_limit") or summary.get("batchLimit"), 100),
        schema_strategy="envelope_json",
    )


def _stream_partitions(
    service: StreamingSourceStore,
    connection: SourceStreamConnection,
    summary: Mapping[str, object],
) -> tuple[int, ...]:
    configured = summary.get("partitions")
    if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        return _validated_partitions(configured)
    if summary.get("partitionMode") == "all":
        topic_name = required_text(summary, "topic")
        topics = service.source_stream_adapter.list_topics(connection, limit=1_000)
        topic = next((item for item in topics if item.topic_name == topic_name), None)
        if topic is None:
            raise NotFound("Kafka topic not found", details={"topic": topic_name})
        if topic.partition_count < 1:
            raise ValidationFailed("Kafka topic has no readable partitions", details={"topic": topic_name})
        return tuple(range(topic.partition_count))
    return (int_value(summary.get("partition"), 0),)


def _validated_partitions(values: Sequence[object]) -> tuple[int, ...]:
    partitions = tuple(int(value) for value in values if isinstance(value, int) and not isinstance(value, bool))
    if not partitions or len(partitions) != len(values) or any(partition < 0 for partition in partitions):
        raise ValidationFailed("invalid Kafka partition selection", details={"partitions": list(values)})
    if len(set(partitions)) != len(partitions):
        raise ValidationFailed("Kafka partition selection contains duplicates", details={"partitions": list(values)})
    return tuple(sorted(partitions))


def _partition_checkpoint_start(run: Mapping[str, object], partition: int) -> dict[str, object]:
    checkpoint = mapping(run.get("checkpoint_start"))
    partitions = checkpoint.get("partitions")
    if isinstance(partitions, Mapping):
        selected = partitions.get(str(partition))
        if isinstance(selected, Mapping):
            return dict(selected)
        if isinstance(selected, int) and not isinstance(selected, bool):
            return {"offset": selected}
    return dict(checkpoint) if checkpoint.get("partition") in {None, partition} else {}


def _subscription(
    request: Mapping[str, object],
    source_name: str,
    topic: str,
) -> SourceStreamSubscription:
    return SourceStreamSubscription(
        stream_name=optional_text(request.get("streamName")) or f"source_{source_name}",
        topic=topic,
        consumer_group=optional_text(request.get("consumerGroup")) or f"foundry-lite-preview-{source_name}",
        partition=int_value(request.get("partition"), 0),
    )


def _subscription_from_config(stream: StreamArchiveConfig) -> SourceStreamSubscription:
    return SourceStreamSubscription(
        stream_name=stream.stream_name,
        topic=stream.topic,
        consumer_group=stream.consumer_group,
        partition=stream.partition,
    )


def _preview_payload(
    subscription: SourceStreamSubscription,
    events: Sequence[StreamEvent],
) -> dict[str, object]:
    return {
        "topic": subscription.topic,
        "partition": subscription.partition,
        "sampleRows": [_event_payload(subscription, event) for event in events],
        "checkpoint": {"offset": events[-1].offset} if events else {},
        "datasetCommitCreated": False,
    }


def _event_payload(subscription: SourceStreamSubscription, event: StreamEvent) -> dict[str, object]:
    return {
        "key": event.key,
        "value": dict(event.payload),
        "topic": subscription.topic,
        "partition": subscription.partition,
        "offset": event.offset,
    }


def _topic_payload(topic: SourceStreamTopic) -> dict[str, object]:
    return {
        "topicName": topic.topic_name,
        "partitionCount": topic.partition_count,
        "isInternal": topic.is_internal,
    }


def _stream_completion(
    stream: StreamArchiveConfig,
    commit: CommitResult | None,
    events: Sequence[StreamEvent],
    lag_observation: StreamLagObservation,
    started: float,
    checkpoint_start: Mapping[str, object],
) -> StreamRunCompletion:
    lag = lag_observation.lag
    checkpoint = _stream_checkpoint(stream, events, checkpoint_start)
    result = {
        "status": "committed" if commit is not None else "no_events",
        "datasetVersionId": commit.version_id if commit is not None else None,
        "rowCount": commit.row_count if commit is not None else 0,
        "topic": stream.topic,
        "partition": stream.partition,
        "consumerGroup": stream.consumer_group,
        "streamName": stream.stream_name,
        "eventCount": len(events),
        "elapsedMs": round((monotonic() - started) * 1000, 2),
        "checkpoint": checkpoint,
        "brokerEndOffset": lag.end_offset if lag is not None else None,
        "brokerLag": lag.lag if lag is not None else None,
        "brokerLagStatus": "measured" if lag is not None else "unavailable",
        "brokerLagError": dict(lag_observation.error) if lag_observation.error else None,
    }
    return StreamRunCompletion(commit.version_id if commit is not None else None, checkpoint, result)


def _combined_stream_completion(
    summary: Mapping[str, object],
    completions: Sequence[StreamPartitionCompletion],
) -> StreamRunCompletion:
    if len(completions) == 1:
        return _single_stream_completion(completions[0])
    checkpoint = _combined_checkpoint(summary, completions)
    partition_results = [dict(item.result) for item in completions]
    version_id = _last_version_id(completions)
    result = _combined_stream_result(summary, completions, partition_results, checkpoint, version_id)
    return StreamRunCompletion(version_id, checkpoint, result)


def _single_stream_completion(completion: StreamPartitionCompletion) -> StreamRunCompletion:
    return StreamRunCompletion(completion.dataset_version_id, completion.checkpoint, completion.result)


def _combined_stream_result(
    summary: Mapping[str, object],
    completions: Sequence[StreamPartitionCompletion],
    partition_results: Sequence[Mapping[str, object]],
    checkpoint: Mapping[str, object],
    version_id: str | None,
) -> dict[str, object]:
    measured_lags = [item.get("brokerLag") for item in partition_results]
    total_lag = _total_lag(measured_lags)
    return {
        "status": "committed" if version_id is not None else "no_events",
        "datasetVersionId": version_id,
        "rowCount": sum(int_value(item.get("rowCount"), 0) for item in partition_results),
        "topic": required_text(summary, "topic"),
        "consumerGroup": required_text(summary, "consumerGroup"),
        "streamName": required_text(summary, "streamName"),
        "partitionCount": len(completions),
        "committedPartitionCount": sum(1 for item in completions if item.dataset_version_id),
        "partitions": partition_results,
        "eventCount": sum(int_value(item.get("eventCount"), 0) for item in partition_results),
        "elapsedMs": round(sum(_float_value(item.get("elapsedMs")) for item in partition_results), 2),
        "checkpoint": checkpoint,
        "brokerLag": total_lag,
        "brokerLagStatus": "measured" if total_lag is not None else "partial",
        "deliveryGuarantee": "AT_LEAST_ONCE",
    }


def _combined_checkpoint(
    summary: Mapping[str, object], completions: Sequence[StreamPartitionCompletion]
) -> dict[str, object]:
    return {
        "topic": required_text(summary, "topic"),
        "consumerGroup": required_text(summary, "consumerGroup"),
        "streamName": required_text(summary, "streamName"),
        "partitionCount": len(completions),
        "partitions": {str(item.partition): dict(item.checkpoint) for item in completions},
    }


def _last_version_id(completions: Sequence[StreamPartitionCompletion]) -> str | None:
    return next((item.dataset_version_id for item in reversed(completions) if item.dataset_version_id), None)


def _total_lag(values: Sequence[object]) -> int | None:
    integer_values: list[int] = []
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        integer_values.append(value)
    return sum(integer_values) if integer_values else None


def _float_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _read_lag(
    service: StreamingSourceStore,
    connection: SourceStreamConnection,
    subscription: SourceStreamSubscription,
    events: Sequence[StreamEvent],
    checkpoint_start: Mapping[str, object],
) -> StreamLagObservation:
    current_offset = events[-1].offset if events else _optional_int(checkpoint_start.get("offset"))
    try:
        return StreamLagObservation(
            service.source_stream_adapter.read_lag(
                connection,
                subscription,
                current_offset=current_offset,
            )
        )
    except FoundryLiteError as exc:
        return StreamLagObservation(
            lag=None,
            error={
                "code": exc.code,
                "message": scrub_error_text(exc.message),
                "details": scrub_error_mapping(exc.details),
            },
        )


def _stream_checkpoint(
    stream: StreamArchiveConfig,
    events: Sequence[StreamEvent],
    checkpoint_start: Mapping[str, object],
) -> dict[str, object]:
    if not events:
        return dict(checkpoint_start)
    return {
        "topic": stream.topic,
        "partition": stream.partition,
        "consumerGroup": stream.consumer_group,
        "offset": events[-1].offset,
    }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int_value(value, 0)
