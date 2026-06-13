from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import StreamAdapter, StreamArchiveConfig
from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.ports.stream_adapter import StreamSchemaStrategy
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.infrastructure.adapters import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@dataclass(frozen=True)
class StreamArchiveWorkerConfig:
    dataset_ref: str
    stream_name: str
    topic: str
    bootstrap_servers: str
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    consumer_group: str = "foundry-lite-archive"
    partition: int = 0
    limit: int = 100
    poll_timeout_seconds: float = 1.0
    max_empty_polls: int = 1
    schema_strategy: StreamSchemaStrategy = "envelope_json"
    cdc_primary_key: tuple[str, ...] = ()
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-stream-archive"
    request_id: str = "req-worker-stream-archive"
    sync_name: str | None = None

    def request_context(self) -> RequestContext:
        return RequestContext(
            tenant_id=self.tenant_id,
            actor_user_id=self.actor_user_id,
            request_id=self.request_id,
            roles=DEMO_ADMIN_ROLES,
        )

    def stream_config(self) -> StreamArchiveConfig:
        return StreamArchiveConfig(
            stream_name=self.stream_name,
            topic=self.topic,
            consumer_group=self.consumer_group,
            partition=self.partition,
            limit=self.limit,
            schema_strategy=self.schema_strategy,
        )

    def stream_adapter(self) -> StreamAdapter:
        kafka_adapter = KafkaStreamAdapter(self.kafka_config())
        if self.schema_strategy == "cdc_envelope_json":
            return DebeziumPostgresStreamAdapter(
                kafka_adapter,
                DebeziumPostgresSourceConfig(primary_key=self.cdc_primary_key),
            )
        return kafka_adapter

    def kafka_config(self) -> KafkaStreamAdapterConfig:
        return KafkaStreamAdapterConfig(
            bootstrap_servers=self.bootstrap_servers,
            consumer_group=self.consumer_group,
            poll_timeout_seconds=self.poll_timeout_seconds,
            max_empty_polls=self.max_empty_polls,
            subscriptions=(
                KafkaStreamSubscription(
                    stream_name=self.stream_name,
                    topic=self.topic,
                    partition=self.partition,
                    default_tenant_id=self.tenant_id,
                ),
            ),
        )


def run_stream_archive_once(
    config: StreamArchiveWorkerConfig,
    *,
    stream_adapter: StreamAdapter | None = None,
) -> CommitResult | None:
    dependencies = create_local_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    adapter = stream_adapter or config.stream_adapter()
    core = FoundryLiteCore(dependencies=replace(dependencies, stream_adapter=adapter))
    ctx = config.request_context()
    core.ensure_dataset(config.dataset_ref, ctx=ctx, primary_key=["event_id"])
    return core.archive_stream_events(
        config.dataset_ref,
        stream=config.stream_config(),
        ctx=ctx,
        sync_name=config.sync_name or f"kafka:{config.stream_name}:{config.consumer_group}",
    )


def config_from_env(env: Mapping[str, str] | None = None) -> StreamArchiveWorkerConfig:
    values = env or os.environ
    storage_root = Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite"))
    return StreamArchiveWorkerConfig(
        dataset_ref=values.get("FOUNDRY_LITE_STREAM_DATASET", "raw.stream_archive"),
        stream_name=values.get("FOUNDRY_LITE_STREAM_NAME", "stream_archive"),
        topic=values.get("FOUNDRY_LITE_KAFKA_TOPIC", "foundry-lite-events"),
        bootstrap_servers=values.get("FOUNDRY_LITE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        storage_root=storage_root,
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        consumer_group=values.get("FOUNDRY_LITE_KAFKA_CONSUMER_GROUP", "foundry-lite-archive"),
        partition=_env_int(values, "FOUNDRY_LITE_KAFKA_PARTITION", 0),
        limit=_env_int(values, "FOUNDRY_LITE_STREAM_ARCHIVE_LIMIT", 100),
        poll_timeout_seconds=_env_float(values, "FOUNDRY_LITE_KAFKA_POLL_TIMEOUT_SECONDS", 1.0),
        max_empty_polls=_env_int(values, "FOUNDRY_LITE_KAFKA_MAX_EMPTY_POLLS", 1),
        schema_strategy=_env_schema_strategy(values),
        cdc_primary_key=_env_csv_tuple(values, "FOUNDRY_LITE_CDC_PRIMARY_KEY"),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        sync_name=values.get("FOUNDRY_LITE_STREAM_SYNC_NAME"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = replace(
        config_from_env(),
        dataset_ref=args.dataset_ref,
        stream_name=args.stream_name,
        topic=args.topic,
        bootstrap_servers=args.bootstrap_servers,
        storage_root=Path(args.storage_root),
        limit=args.limit,
        poll_timeout_seconds=args.poll_timeout_seconds,
        max_empty_polls=args.max_empty_polls,
        schema_strategy=args.schema_strategy,
        cdc_primary_key=_csv_tuple(args.cdc_primary_key),
    )
    try:
        result = run_stream_archive_once(config)
    except AdapterError as exc:
        print(json.dumps(adapter_failure_payload(exc), sort_keys=True))
        return 1
    print(_result_json(result))
    return 0


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Archive one Kafka/Redpanda stream micro-batch into a raw dataset.")
    parser.add_argument("--dataset-ref", default=defaults.dataset_ref)
    parser.add_argument("--stream-name", default=defaults.stream_name)
    parser.add_argument("--topic", default=defaults.topic)
    parser.add_argument("--bootstrap-servers", default=defaults.bootstrap_servers)
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--limit", type=int, default=defaults.limit)
    parser.add_argument("--poll-timeout-seconds", type=float, default=defaults.poll_timeout_seconds)
    parser.add_argument("--max-empty-polls", type=int, default=defaults.max_empty_polls)
    parser.add_argument(
        "--schema-strategy", choices=["envelope_json", "cdc_envelope_json"], default=defaults.schema_strategy
    )
    parser.add_argument("--cdc-primary-key", default=",".join(defaults.cdc_primary_key))
    return parser


def _result_json(result: CommitResult | None) -> str:
    payload: Mapping[str, object]
    if result is None:
        payload = {"status": "NO_EVENTS"}
    else:
        payload = {
            "status": "ARCHIVED",
            "versionId": result.version_id,
            "versionNumber": result.version_number,
            "rowCount": result.row_count,
        }
    return json.dumps(payload, sort_keys=True)


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw_value = values.get(name)
    return default if raw_value is None else int(raw_value)


def _env_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw_value = values.get(name)
    return default if raw_value is None else float(raw_value)


def _env_schema_strategy(values: Mapping[str, str]) -> StreamSchemaStrategy:
    raw_value = values.get("FOUNDRY_LITE_STREAM_SCHEMA_STRATEGY", "envelope_json")
    if raw_value in {"envelope_json", "cdc_envelope_json"}:
        return cast(StreamSchemaStrategy, raw_value)
    raise ValueError(f"unsupported stream schema strategy: {raw_value}")


def _env_csv_tuple(values: Mapping[str, str], name: str) -> tuple[str, ...]:
    return _csv_tuple(values.get(name, ""))


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


if __name__ == "__main__":
    raise SystemExit(main())
