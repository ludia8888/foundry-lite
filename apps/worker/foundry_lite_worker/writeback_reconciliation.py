from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from foundry_lite.application.action_types import ActionWritebackRecoveryResult
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.infrastructure.adapters.s3_external_writeback import (
    S3ExternalWritebackAdapter,
    S3ExternalWritebackConfig,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@dataclass(frozen=True)
class WritebackReconciliationWorkerConfig:
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    external_writeback_profile: str = "none"
    external_writeback_bucket: str | None = None
    limit: int = 50
    max_batches: int = 1
    max_empty_batches: int = 1
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-writeback-reconciliation"
    request_id: str = "req-worker-writeback-reconciliation"
    evidence_path: Path | None = None

    def request_context(self, *, batch_number: int) -> RequestContext:
        return RequestContext(
            tenant_id=_required_value("tenant_id", self.tenant_id),
            actor_user_id=_required_value("actor_user_id", self.actor_user_id),
            request_id=f"{_required_value('request_id', self.request_id)}:batch-{batch_number}",
            roles=DEMO_ADMIN_ROLES,
        )


@dataclass(frozen=True)
class WritebackReconciliationWorkerResult:
    status: Literal["STOPPED"]
    stop_reason: Literal["max_batches", "empty_batches"]
    iterations: int
    empty_batches: int
    processed: int
    reconciled: int
    skipped: int
    failed: int
    writeback_ids: tuple[str, ...]


@dataclass(frozen=True)
class WritebackReconciliationWorkerRuntime:
    foundry: FoundryLite


def run_writeback_reconciliation_batches(
    config: WritebackReconciliationWorkerConfig,
    *,
    runtime: WritebackReconciliationWorkerRuntime | None = None,
) -> WritebackReconciliationWorkerResult:
    active_runtime = runtime or build_writeback_reconciliation_runtime(config)
    accumulator = _RecoveryAccumulator()
    empty_batches = 0
    for batch_number in range(1, _positive(config.max_batches, "max_batches") + 1):
        result = active_runtime.foundry.operations.recover_action_writebacks(
            ctx=config.request_context(batch_number=batch_number),
            limit=_positive(config.limit, "limit"),
        )
        accumulator.add(result)
        empty_batches = empty_batches + 1 if result["processed"] == 0 else 0
        if empty_batches >= _positive(config.max_empty_batches, "max_empty_batches"):
            return _finalize(config, accumulator, batch_number, empty_batches, "empty_batches")
    return _finalize(config, accumulator, accumulator.iterations, empty_batches, "max_batches")


def build_writeback_reconciliation_runtime(
    config: WritebackReconciliationWorkerConfig,
) -> WritebackReconciliationWorkerRuntime:
    dependencies = create_local_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    foundry = FoundryLite(dependencies=dependencies)
    if adapter := _external_writeback_adapter(config):
        foundry.actions._action.set_external_writeback_adapter(adapter)
    return WritebackReconciliationWorkerRuntime(foundry=foundry)


def config_from_env(env: Mapping[str, str] | None = None) -> WritebackReconciliationWorkerConfig:
    values = env or os.environ
    return WritebackReconciliationWorkerConfig(
        storage_root=Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite")),
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        external_writeback_profile=values.get("FOUNDRY_LITE_EXTERNAL_WRITEBACK_PROFILE", "none"),
        external_writeback_bucket=values.get("FOUNDRY_LITE_EXTERNAL_WRITEBACK_BUCKET"),
        limit=_env_int(values, "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_LIMIT", 50),
        max_batches=_env_int(values, "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_MAX_BATCHES", 1),
        max_empty_batches=_env_int(values, "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_MAX_EMPTY_BATCHES", 1),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        actor_user_id=values.get("FOUNDRY_LITE_WORKER_ACTOR_USER_ID", "worker-writeback-reconciliation"),
        request_id=values.get("FOUNDRY_LITE_WORKER_REQUEST_ID", "req-worker-writeback-reconciliation"),
        evidence_path=_optional_path(values, "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_EVIDENCE_PATH"),
    )


def main(argv: list[str] | None = None) -> int:
    config: WritebackReconciliationWorkerConfig | None = None
    try:
        parser = _parser()
        config = _config_from_args(parser.parse_args(argv))
        result = run_writeback_reconciliation_batches(config)
    except (FoundryLiteError, ValueError) as exc:
        print(_failure_json(exc, config))
        return 1
    print(_result_json(result))
    return 0


class _RecoveryAccumulator:
    def __init__(self) -> None:
        self.iterations = 0
        self.processed = 0
        self.reconciled = 0
        self.skipped = 0
        self.failed = 0
        self.writeback_ids: list[str] = []

    def add(self, result: ActionWritebackRecoveryResult) -> None:
        self.iterations += 1
        self.processed += result["processed"]
        self.reconciled += result["reconciled"]
        self.skipped += result["skipped"]
        self.failed += result["failed"]
        self.writeback_ids.extend(str(item["writebackId"]) for item in result["items"])


def _external_writeback_adapter(config: WritebackReconciliationWorkerConfig) -> S3ExternalWritebackAdapter | None:
    if config.external_writeback_profile == "none":
        return None
    if config.external_writeback_profile != "s3-external-writeback":
        raise ValueError(f"unknown external writeback profile: {config.external_writeback_profile}")
    if not config.external_writeback_bucket:
        raise ValueError("s3-external-writeback requires FOUNDRY_LITE_EXTERNAL_WRITEBACK_BUCKET")
    return S3ExternalWritebackAdapter(
        S3ExternalWritebackConfig(
            bucket=config.external_writeback_bucket,
            endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
            access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
            secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
            region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        )
    )


def _finalize(
    config: WritebackReconciliationWorkerConfig,
    accumulator: _RecoveryAccumulator,
    iterations: int,
    empty_batches: int,
    stop_reason: Literal["max_batches", "empty_batches"],
) -> WritebackReconciliationWorkerResult:
    result = WritebackReconciliationWorkerResult(
        status="STOPPED",
        stop_reason=stop_reason,
        iterations=iterations,
        empty_batches=empty_batches,
        processed=accumulator.processed,
        reconciled=accumulator.reconciled,
        skipped=accumulator.skipped,
        failed=accumulator.failed,
        writeback_ids=tuple(accumulator.writeback_ids),
    )
    _write_evidence(config, result)
    return result


def _write_evidence(config: WritebackReconciliationWorkerConfig, result: WritebackReconciliationWorkerResult) -> None:
    if config.evidence_path is None:
        return
    config.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    config.evidence_path.write_text(_result_json(result) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Resolve action writeback reconciliation rows by remote lookup.")
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--db-url", default=defaults.db_url)
    parser.add_argument("--adapter-profile", default=defaults.adapter_profile)
    parser.add_argument("--external-writeback-profile", default=defaults.external_writeback_profile)
    parser.add_argument("--external-writeback-bucket", default=defaults.external_writeback_bucket)
    parser.add_argument("--limit", type=int, default=defaults.limit)
    parser.add_argument("--max-batches", type=int, default=defaults.max_batches)
    parser.add_argument("--max-empty-batches", type=int, default=defaults.max_empty_batches)
    parser.add_argument("--evidence-path", default=str(defaults.evidence_path) if defaults.evidence_path else None)
    return parser


def _config_from_args(args: argparse.Namespace) -> WritebackReconciliationWorkerConfig:
    return replace(
        config_from_env(),
        storage_root=Path(args.storage_root),
        db_url=args.db_url,
        adapter_profile=args.adapter_profile,
        external_writeback_profile=args.external_writeback_profile,
        external_writeback_bucket=args.external_writeback_bucket,
        limit=args.limit,
        max_batches=args.max_batches,
        max_empty_batches=args.max_empty_batches,
        evidence_path=Path(args.evidence_path) if args.evidence_path else None,
    )


def _result_json(result: WritebackReconciliationWorkerResult) -> str:
    return json.dumps(
        {
            "status": result.status,
            "stopReason": result.stop_reason,
            "iterations": result.iterations,
            "emptyBatches": result.empty_batches,
            "processed": result.processed,
            "reconciled": result.reconciled,
            "skipped": result.skipped,
            "failed": result.failed,
            "writebackIds": list(result.writeback_ids),
        },
        sort_keys=True,
    )


def _failure_json(exc: Exception, config: WritebackReconciliationWorkerConfig | None) -> str:
    return json.dumps(_failure_payload(exc, config), sort_keys=True)


def _failure_payload(exc: Exception, config: WritebackReconciliationWorkerConfig | None) -> Mapping[str, object]:
    if isinstance(exc, ValueError):
        return runtime_error_payload(exc, _failure_context(config), adapter="writeback_reconciliation_worker")
    return runtime_error_payload(exc, _failure_context(config), adapter="writeback_reconciliation_worker")


def _failure_context(config: WritebackReconciliationWorkerConfig | None) -> RequestContext | None:
    if config is None:
        return None
    return RequestContext(
        tenant_id=config.tenant_id,
        actor_user_id=config.actor_user_id,
        request_id=config.request_id,
        roles=DEMO_ADMIN_ROLES,
    )


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    return int(values.get(name, str(default)))


def _optional_path(values: Mapping[str, str], name: str) -> Path | None:
    value = values.get(name)
    return Path(value) if value else None


def _positive(value: int, field: str) -> int:
    if value < 1:
        raise ValueError(f"writeback reconciliation worker {field} must be positive")
    return value


def _required_value(field: str, value: str) -> str:
    if not value:
        raise ValueError(f"writeback reconciliation worker requires {field}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
