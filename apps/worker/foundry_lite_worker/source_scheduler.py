from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload, scrub_error_text
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies


@dataclass(frozen=True)
class SourceSchedulerWorkerConfig:
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    max_runs_per_tick: int = 50
    max_ticks: int = 0
    max_empty_ticks: int = 0
    tick_interval_seconds: float = 60.0
    is_continuous: bool = False
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-source-scheduler"
    request_id: str = "req-worker-source-scheduler"
    evidence_path: Path | None = None

    def request_context(self, *, tick_number: int) -> RequestContext:
        return RequestContext(
            tenant_id=_required_value("tenant_id", self.tenant_id),
            actor_user_id=_required_value("actor_user_id", self.actor_user_id),
            request_id=f"{_required_value('request_id', self.request_id)}:tick-{tick_number}",
            roles=DEMO_ADMIN_ROLES,
        )


@dataclass(frozen=True)
class SourceSchedulerWorkerResult:
    status: Literal["STOPPED"]
    stop_reason: Literal["max_ticks", "empty_ticks", "stop_requested"]
    iterations: int
    empty_ticks: int
    evaluated: int
    due: int
    started: int
    run_ids: tuple[str, ...]


def run_source_scheduler_once(config: SourceSchedulerWorkerConfig) -> SourceSchedulerWorkerResult:
    runtime = _build_foundry(config)
    try:
        result = _run_tick(runtime, config, tick_number=1)
        summary = _summary("max_ticks", 1, 0 if _started_count(result) else 1, [result])
        _write_evidence(config, summary)
        return summary
    finally:
        runtime.close()


def run_source_scheduler_daemon(
    config: SourceSchedulerWorkerConfig,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> SourceSchedulerWorkerResult:
    runtime = _build_foundry(config)
    try:
        return _run_daemon(runtime, config, should_stop=should_stop)
    finally:
        runtime.close()


def _run_daemon(
    runtime: FoundryLite,
    config: SourceSchedulerWorkerConfig,
    *,
    should_stop: Callable[[], bool] | None,
) -> SourceSchedulerWorkerResult:
    stop_requested = should_stop or _never_stop
    results: list[Mapping[str, object]] = []
    empty_ticks = 0
    max_ticks = _positive_or_unbounded(config.max_ticks, "max_ticks")
    max_empty_ticks = _positive_or_unbounded(config.max_empty_ticks, "max_empty_ticks")
    tick_number = 1
    while True:
        if max_ticks is not None and tick_number > max_ticks:
            return _finalize(config, "max_ticks", len(results), empty_ticks, results)
        if stop_requested():
            return _finalize(config, "stop_requested", tick_number - 1, empty_ticks, results)
        tick = _run_tick(runtime, config, tick_number=tick_number)
        results.append(tick)
        empty_ticks = empty_ticks + 1 if _started_count(tick) == 0 else 0
        if max_empty_ticks is not None and empty_ticks >= max_empty_ticks:
            return _finalize(config, "empty_ticks", tick_number, empty_ticks, results)
        _sleep_between_ticks(config, tick_number, max_ticks=max_ticks)
        tick_number += 1


def config_from_env(env: Mapping[str, str] | None = None) -> SourceSchedulerWorkerConfig:
    values = env or os.environ
    return SourceSchedulerWorkerConfig(
        storage_root=Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite")),
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        max_runs_per_tick=_env_int(values, "FOUNDRY_LITE_SOURCE_SCHEDULER_MAX_RUNS", 50),
        max_ticks=_env_int(values, "FOUNDRY_LITE_SOURCE_SCHEDULER_MAX_TICKS", 0),
        max_empty_ticks=_env_int(values, "FOUNDRY_LITE_SOURCE_SCHEDULER_MAX_EMPTY_TICKS", 0),
        tick_interval_seconds=_env_float(values, "FOUNDRY_LITE_SOURCE_SCHEDULER_INTERVAL_SECONDS", 60.0),
        is_continuous=_env_bool(values, "FOUNDRY_LITE_SOURCE_SCHEDULER_CONTINUOUS", False),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        actor_user_id=values.get("FOUNDRY_LITE_WORKER_ACTOR_USER_ID", "worker-source-scheduler"),
        request_id=values.get("FOUNDRY_LITE_WORKER_REQUEST_ID", "req-worker-source-scheduler"),
        evidence_path=_optional_path(values, "FOUNDRY_LITE_SOURCE_SCHEDULER_EVIDENCE_PATH"),
    )


def main(argv: list[str] | None = None) -> int:
    config: SourceSchedulerWorkerConfig | None = None
    try:
        parser = _parser()
        config = _config_from_args(parser.parse_args(argv))
        result = run_source_scheduler_daemon(config) if config.is_continuous else run_source_scheduler_once(config)
    except (FoundryLiteError, ValueError) as exc:
        print(_failure_json(exc, config))
        return 1
    print(_result_json(result))
    return 0


def _run_tick(runtime: FoundryLite, config: SourceSchedulerWorkerConfig, *, tick_number: int) -> Mapping[str, object]:
    return runtime.sources.run_due_managed_syncs(
        ctx=config.request_context(tick_number=tick_number),
        max_runs=config.max_runs_per_tick,
    )


def _finalize(
    config: SourceSchedulerWorkerConfig,
    stop_reason: Literal["max_ticks", "empty_ticks", "stop_requested"],
    iterations: int,
    empty_ticks: int,
    results: list[Mapping[str, object]],
) -> SourceSchedulerWorkerResult:
    summary = _summary(stop_reason, iterations, empty_ticks, results)
    _write_evidence(config, summary)
    return summary


def _summary(
    stop_reason: Literal["max_ticks", "empty_ticks", "stop_requested"],
    iterations: int,
    empty_ticks: int,
    results: list[Mapping[str, object]],
) -> SourceSchedulerWorkerResult:
    return SourceSchedulerWorkerResult(
        status="STOPPED",
        stop_reason=stop_reason,
        iterations=iterations,
        empty_ticks=empty_ticks,
        evaluated=sum(_int_field(row, "evaluated") for row in results),
        due=sum(len(_list_field(row, "due")) for row in results),
        started=sum(_started_count(row) for row in results),
        run_ids=tuple(_started_run_ids(results)),
    )


def _started_run_ids(results: list[Mapping[str, object]]) -> list[str]:
    ids: list[str] = []
    for result in results:
        for row in _list_field(result, "started"):
            run = row.get("run") if isinstance(row, Mapping) else None
            run_id = run.get("runId") if isinstance(run, Mapping) else None
            if isinstance(run_id, str):
                ids.append(run_id)
    return ids


def _sleep_between_ticks(config: SourceSchedulerWorkerConfig, tick_number: int, *, max_ticks: int | None) -> None:
    if max_ticks is None or tick_number < max_ticks:
        time.sleep(max(0.0, config.tick_interval_seconds))


def _build_foundry(config: SourceSchedulerWorkerConfig) -> FoundryLite:
    dependencies = create_runtime_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    return FoundryLite(dependencies=dependencies, should_initialize_schema=False)


def _write_evidence(config: SourceSchedulerWorkerConfig, result: SourceSchedulerWorkerResult) -> None:
    if config.evidence_path is None:
        return
    config.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    config.evidence_path.write_text(_result_json(result) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Run due Foundry-lite Source managed sync schedules.")
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--db-url", default=defaults.db_url)
    parser.add_argument("--adapter-profile", default=defaults.adapter_profile)
    parser.add_argument("--max-runs", type=int, default=defaults.max_runs_per_tick)
    parser.add_argument("--max-ticks", type=int, default=defaults.max_ticks)
    parser.add_argument("--max-empty-ticks", type=int, default=defaults.max_empty_ticks)
    parser.add_argument("--interval-seconds", type=float, default=defaults.tick_interval_seconds)
    parser.add_argument("--continuous", action="store_true", default=defaults.is_continuous, dest="is_continuous")
    parser.add_argument("--evidence-path", default=str(defaults.evidence_path) if defaults.evidence_path else None)
    return parser


def _config_from_args(args: argparse.Namespace) -> SourceSchedulerWorkerConfig:
    return replace(
        config_from_env(),
        storage_root=Path(args.storage_root),
        db_url=args.db_url,
        adapter_profile=args.adapter_profile,
        max_runs_per_tick=args.max_runs,
        max_ticks=args.max_ticks,
        max_empty_ticks=args.max_empty_ticks,
        tick_interval_seconds=args.interval_seconds,
        is_continuous=args.is_continuous,
        evidence_path=Path(args.evidence_path) if args.evidence_path else None,
    )


def _failure_json(exc: Exception, config: SourceSchedulerWorkerConfig | None) -> str:
    return json.dumps(_failure_payload(exc, config), sort_keys=True)


def _failure_payload(exc: Exception, config: SourceSchedulerWorkerConfig | None) -> Mapping[str, object]:
    if isinstance(exc, ValueError):
        return {
            "type": "CONFIGURATION_ERROR",
            "message": scrub_error_text(str(exc)),
            "details": _failure_trace(config),
        }
    return runtime_error_payload(exc, _failure_context(config), adapter="source_scheduler_worker")


def _failure_trace(config: SourceSchedulerWorkerConfig | None) -> Mapping[str, str]:
    ctx = _failure_context(config)
    if ctx is None:
        return {"adapter": "source_scheduler_worker"}
    return {
        "tenant_id": ctx.tenant_id,
        "actor_user_id": ctx.actor_user_id,
        "request_id": ctx.request_id,
        "correlation_id": ctx.request_id,
        "adapter": "source_scheduler_worker",
    }


def _failure_context(config: SourceSchedulerWorkerConfig | None) -> RequestContext | None:
    if config is None:
        return None
    try:
        return config.request_context(tick_number=0)
    except ValueError:
        return None


def _result_json(result: SourceSchedulerWorkerResult) -> str:
    return json.dumps(
        {
            "status": result.status,
            "stopReason": result.stop_reason,
            "iterations": result.iterations,
            "emptyTicks": result.empty_ticks,
            "evaluated": result.evaluated,
            "due": result.due,
            "started": result.started,
            "runIds": list(result.run_ids),
        },
        sort_keys=True,
    )


def _list_field(row: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    value = row.get(field)
    return list(value) if isinstance(value, list) else []


def _started_count(result: Mapping[str, object]) -> int:
    return len(_list_field(result, "started"))


def _int_field(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    return value if isinstance(value, int) else 0


def _required_value(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"source scheduler worker requires {field}")
    return normalized


def _positive(value: int) -> int:
    if value <= 0:
        raise ValueError("source scheduler worker numeric config must be positive")
    return value


def _positive_or_unbounded(value: int, field: str) -> int | None:
    if value < 0:
        raise ValueError(f"source scheduler worker {field} cannot be negative")
    return value or None


def _env_int(values: Mapping[str, str], key: str, default: int) -> int:
    return int(values.get(key, str(default)))


def _env_float(values: Mapping[str, str], key: str, default: float) -> float:
    return float(values.get(key, str(default)))


def _env_bool(values: Mapping[str, str], key: str, is_default: bool) -> bool:
    raw = values.get(key)
    if raw is None:
        return is_default
    return raw.lower() in {"1", "true", "yes", "on"}


def _optional_path(values: Mapping[str, str], key: str) -> Path | None:
    value = values.get(key)
    return Path(value) if value else None


def _never_stop() -> bool:
    return False
