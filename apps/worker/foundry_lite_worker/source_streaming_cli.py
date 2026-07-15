"""Command-line entrypoint for the durable Source streaming service."""

from __future__ import annotations

import argparse
import json
import os
import signal
from collections.abc import Mapping
from pathlib import Path
from threading import Event

from foundry_lite.domain.context import DEFAULT_TENANT_ID
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_worker.source_streaming import (
    SourceStreamingServiceConfig,
    SourceStreamingServiceResult,
    run_source_streaming_service,
)
from foundry_lite_worker.source_streaming_supervisor import (
    SourceStreamingSupervisorResult,
    run_source_streaming_supervisor,
)
from foundry_lite_worker.source_streaming_support import worker_error_payload


def config_from_env(env: Mapping[str, str] | None = None) -> SourceStreamingServiceConfig:
    values = env or os.environ
    return SourceStreamingServiceConfig(
        sync_name=values.get("FOUNDRY_LITE_STREAMING_SYNC_NAME", "kraken_btc_usd_live_kafka_sync"),
        storage_root=Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite")),
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        actor_user_id=values.get("FOUNDRY_LITE_STREAMING_ACTOR_USER_ID", "worker-source-streaming"),
        request_id=values.get("FOUNDRY_LITE_STREAMING_REQUEST_ID", "req-worker-source-streaming"),
        worker_id=values.get("FOUNDRY_LITE_STREAMING_WORKER_ID", "source-streaming-worker"),
        lease_ttl_seconds=_env_int(values, "FOUNDRY_LITE_STREAMING_LEASE_TTL_SECONDS", 30),
        retry_base_seconds=_env_float(values, "FOUNDRY_LITE_STREAMING_RETRY_BASE_SECONDS", 1.0),
        retry_max_seconds=_env_float(values, "FOUNDRY_LITE_STREAMING_RETRY_MAX_SECONDS", 30.0),
        max_iterations=_env_optional_int(values, "FOUNDRY_LITE_STREAMING_MAX_ITERATIONS"),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stop_event = Event()
    _install_signal_handlers(stop_event)
    try:
        result = _run_requested_mode(args, stop_event)
    except (FoundryLiteError, ValueError) as exc:
        print(json.dumps(worker_error_payload(exc), sort_keys=True))
        return 1
    print(json.dumps(_result_payload(result), sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Run one durable Kafka streaming Source service.")
    parser.add_argument("--sync-name", default=defaults.sync_name)
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--db-url", default=defaults.db_url)
    parser.add_argument("--worker-id", default=defaults.worker_id)
    parser.add_argument("--lease-ttl-seconds", type=int, default=defaults.lease_ttl_seconds)
    parser.add_argument("--max-iterations", type=int, default=defaults.max_iterations)
    parser.add_argument("--once", action="store_true", help="Exit after the current lifecycle run stops.")
    return parser


def _config_from_args(args: argparse.Namespace) -> SourceStreamingServiceConfig:
    defaults = config_from_env()
    return SourceStreamingServiceConfig(
        sync_name=args.sync_name,
        storage_root=Path(args.storage_root),
        db_url=args.db_url,
        adapter_profile=defaults.adapter_profile,
        tenant_id=defaults.tenant_id,
        actor_user_id=defaults.actor_user_id,
        request_id=defaults.request_id,
        worker_id=args.worker_id,
        lease_ttl_seconds=args.lease_ttl_seconds,
        retry_base_seconds=defaults.retry_base_seconds,
        retry_max_seconds=defaults.retry_max_seconds,
        max_iterations=args.max_iterations,
    )


def _install_signal_handlers(stop_event: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def _run_requested_mode(
    args: argparse.Namespace, stop_event: Event
) -> SourceStreamingServiceResult | SourceStreamingSupervisorResult:
    config = _config_from_args(args)
    if args.once:
        return run_source_streaming_service(config, stop_event=stop_event)
    return run_source_streaming_supervisor(config, stop_event=stop_event)


def _result_payload(
    result: SourceStreamingServiceResult | SourceStreamingSupervisorResult,
) -> dict[str, object]:
    if isinstance(result, SourceStreamingSupervisorResult):
        return _supervisor_result_payload(result)
    return {
        "status": "STOPPED",
        "workflowRunId": result.workflow_run_id,
        "stopReason": result.stop_reason,
        "iterations": result.iterations,
        "archivedBatches": result.archived_batches,
        "rowsArchived": result.rows_archived,
        "lastVersionId": result.last_version_id,
    }


def _supervisor_result_payload(result: SourceStreamingSupervisorResult) -> dict[str, object]:
    last = result.last_service_result
    return {
        "status": "STOPPED",
        "mode": "supervisor",
        "stopReason": result.stop_reason,
        "lifecycleRuns": result.lifecycle_runs,
        "lastWorkflowRunId": last.workflow_run_id if last is not None else None,
        "lastVersionId": last.last_version_id if last is not None else None,
    }


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    return int(values.get(name, str(default)))


def _env_float(values: Mapping[str, str], name: str, default: float) -> float:
    return float(values.get(name, str(default)))


def _env_optional_int(values: Mapping[str, str], name: str) -> int | None:
    value = values.get(name)
    return int(value) if value is not None and value.strip() else None


if __name__ == "__main__":
    raise SystemExit(main())
