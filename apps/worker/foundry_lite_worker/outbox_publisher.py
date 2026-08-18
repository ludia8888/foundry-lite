from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.outbox_publisher_service import (
    DEFAULT_OUTBOX_STREAM_NAME,
    OutboxPublishBatchResult,
)
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload, scrub_error_text
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies


@dataclass(frozen=True)
class OutboxPublisherWorkerConfig:
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    stream_name: str = DEFAULT_OUTBOX_STREAM_NAME
    limit: int = 100
    max_batches: int = 1
    max_empty_batches: int = 1
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-outbox-publisher"
    request_id: str = "req-worker-outbox-publisher"
    evidence_path: Path | None = None

    def request_context(self, *, batch_number: int) -> RequestContext:
        request_id = f"{_required_value('request_id', self.request_id)}:batch-{batch_number}"
        return RequestContext(
            tenant_id=_required_value("tenant_id", self.tenant_id),
            actor_user_id=_required_value("actor_user_id", self.actor_user_id),
            request_id=request_id,
            roles=DEMO_ADMIN_ROLES,
        )


@dataclass(frozen=True)
class OutboxPublisherWorkerResult:
    status: Literal["STOPPED"]
    stop_reason: Literal["max_batches", "empty_batches"]
    stream_name: str
    iterations: int
    empty_batches: int
    requested: int
    published: int
    failed: int
    skipped: int
    event_ids: tuple[str, ...]
    dead_letter_event_ids: tuple[str, ...]


def publish_outbox_batches(config: OutboxPublisherWorkerConfig) -> OutboxPublisherWorkerResult:
    runtime = _build_foundry(config)
    try:
        return _publish_batches(runtime, config)
    finally:
        runtime.close()


def _publish_batches(runtime: FoundryLite, config: OutboxPublisherWorkerConfig) -> OutboxPublisherWorkerResult:
    accumulator = _OutboxAccumulator(config.stream_name)
    empty_batches = 0
    for batch_number in range(1, _positive(config.max_batches) + 1):
        result = runtime.operations.publish_pending_outbox(
            ctx=config.request_context(batch_number=batch_number),
            stream_name=config.stream_name,
            limit=_positive(config.limit),
        )
        accumulator.add(result)
        if _is_empty_batch(result):
            empty_batches += 1
            if empty_batches >= _positive(config.max_empty_batches):
                return _finalize(config, accumulator, batch_number, empty_batches, "empty_batches")
            continue
        empty_batches = 0
    return _finalize(config, accumulator, accumulator.iterations, empty_batches, "max_batches")


def config_from_env(env: Mapping[str, str] | None = None) -> OutboxPublisherWorkerConfig:
    values = env or os.environ
    return OutboxPublisherWorkerConfig(
        storage_root=Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite")),
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        stream_name=values.get("FOUNDRY_LITE_OUTBOX_STREAM_NAME", DEFAULT_OUTBOX_STREAM_NAME),
        limit=_env_int(values, "FOUNDRY_LITE_OUTBOX_LIMIT", 100),
        max_batches=_env_int(values, "FOUNDRY_LITE_OUTBOX_MAX_BATCHES", 1),
        max_empty_batches=_env_int(values, "FOUNDRY_LITE_OUTBOX_MAX_EMPTY_BATCHES", 1),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        actor_user_id=values.get("FOUNDRY_LITE_WORKER_ACTOR_USER_ID", "worker-outbox-publisher"),
        request_id=values.get("FOUNDRY_LITE_WORKER_REQUEST_ID", "req-worker-outbox-publisher"),
        evidence_path=_optional_path(values, "FOUNDRY_LITE_OUTBOX_EVIDENCE_PATH"),
    )


def main(argv: list[str] | None = None) -> int:
    config: OutboxPublisherWorkerConfig | None = None
    try:
        parser = _parser()
        config = _config_from_args(parser.parse_args(argv))
        result = publish_outbox_batches(config)
    except (FoundryLiteError, ValueError) as exc:
        print(_failure_json(exc, config))
        return 1
    print(_result_json(result))
    return 0


class _OutboxAccumulator:
    def __init__(self, stream_name: str) -> None:
        self.stream_name = stream_name
        self.iterations = 0
        self.requested = 0
        self.published = 0
        self.failed = 0
        self.skipped = 0
        self.event_ids: list[str] = []
        self.dead_letter_event_ids: list[str] = []

    def add(self, result: OutboxPublishBatchResult) -> None:
        self.iterations += 1
        self.requested += result["requested"]
        self.published += result["published"]
        self.failed += result["failed"]
        self.skipped += result["skipped"]
        self.event_ids.extend(result["eventIds"])
        self.dead_letter_event_ids.extend(result["deadLetterEventIds"])


def _build_foundry(config: OutboxPublisherWorkerConfig) -> FoundryLite:
    dependencies = create_runtime_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    return FoundryLite(dependencies=dependencies, should_initialize_schema=False)


def _finalize(
    config: OutboxPublisherWorkerConfig,
    accumulator: _OutboxAccumulator,
    iterations: int,
    empty_batches: int,
    stop_reason: Literal["max_batches", "empty_batches"],
) -> OutboxPublisherWorkerResult:
    result = OutboxPublisherWorkerResult(
        status="STOPPED",
        stop_reason=stop_reason,
        stream_name=accumulator.stream_name,
        iterations=iterations,
        empty_batches=empty_batches,
        requested=accumulator.requested,
        published=accumulator.published,
        failed=accumulator.failed,
        skipped=accumulator.skipped,
        event_ids=tuple(accumulator.event_ids),
        dead_letter_event_ids=tuple(accumulator.dead_letter_event_ids),
    )
    _write_evidence(config, result)
    return result


def _write_evidence(config: OutboxPublisherWorkerConfig, result: OutboxPublisherWorkerResult) -> None:
    if config.evidence_path is None:
        return
    config.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    config.evidence_path.write_text(_result_json(result) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Publish pending Foundry-lite outbox rows through the stream adapter.")
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--db-url", default=defaults.db_url)
    parser.add_argument("--adapter-profile", default=defaults.adapter_profile)
    parser.add_argument("--stream-name", default=defaults.stream_name)
    parser.add_argument("--limit", type=int, default=defaults.limit)
    parser.add_argument("--max-batches", type=int, default=defaults.max_batches)
    parser.add_argument("--max-empty-batches", type=int, default=defaults.max_empty_batches)
    parser.add_argument("--evidence-path", default=str(defaults.evidence_path) if defaults.evidence_path else None)
    return parser


def _config_from_args(args: argparse.Namespace) -> OutboxPublisherWorkerConfig:
    evidence_path = Path(args.evidence_path) if args.evidence_path else None
    return replace(
        config_from_env(),
        storage_root=Path(args.storage_root),
        db_url=args.db_url,
        adapter_profile=args.adapter_profile,
        stream_name=args.stream_name,
        limit=args.limit,
        max_batches=args.max_batches,
        max_empty_batches=args.max_empty_batches,
        evidence_path=evidence_path,
    )


def _result_json(result: OutboxPublisherWorkerResult) -> str:
    return json.dumps(
        {
            "status": result.status,
            "stopReason": result.stop_reason,
            "streamName": result.stream_name,
            "iterations": result.iterations,
            "emptyBatches": result.empty_batches,
            "requested": result.requested,
            "published": result.published,
            "failed": result.failed,
            "skipped": result.skipped,
            "eventIds": list(result.event_ids),
            "deadLetterEventIds": list(result.dead_letter_event_ids),
        },
        sort_keys=True,
    )


def _failure_json(exc: Exception, config: OutboxPublisherWorkerConfig | None) -> str:
    return json.dumps(_failure_payload(exc, config), sort_keys=True)


def _failure_payload(exc: Exception, config: OutboxPublisherWorkerConfig | None) -> Mapping[str, object]:
    if isinstance(exc, ValueError):
        payload: dict[str, object] = {
            "type": "CONFIGURATION_ERROR",
            "message": scrub_error_text(str(exc)),
            "details": {},
        }
        if trace := _failure_trace(config):
            payload["trace"] = trace
        return payload
    return runtime_error_payload(exc, _failure_context(config), adapter="outbox_publisher_worker")


def _failure_trace(config: OutboxPublisherWorkerConfig | None) -> Mapping[str, str]:
    ctx = _failure_context(config)
    if ctx is None:
        return {"adapter": "outbox_publisher_worker"}
    return {
        "tenant_id": ctx.tenant_id,
        "actor_user_id": ctx.actor_user_id,
        "request_id": ctx.request_id,
        "correlation_id": ctx.request_id,
        "adapter": "outbox_publisher_worker",
    }


def _failure_context(config: OutboxPublisherWorkerConfig | None) -> RequestContext | None:
    if config is None:
        return None
    try:
        return config.request_context(batch_number=1)
    except ValueError:
        return None


def _is_empty_batch(result: OutboxPublishBatchResult) -> bool:
    return result["requested"] == 0


def _positive(value: int) -> int:
    return max(value, 1)


def _required_value(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"outbox publisher worker requires {field}")
    return normalized


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw_value = values.get(name)
    return default if raw_value is None else int(raw_value)


def _optional_path(values: Mapping[str, str], name: str) -> Path | None:
    value = values.get(name)
    return Path(value) if value else None


if __name__ == "__main__":
    raise SystemExit(main())
