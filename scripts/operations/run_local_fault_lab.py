"""Run local distributed-failure drills against real local infrastructure.

This is intentionally not part of the default CI lane. It is a laptop-scale
fault lab for operator rehearsal: Spark executor loss, Kafka/PostgreSQL cursor
CAS storms, worker restart replay, and honest boundary evidence for failures
that require managed infrastructure.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess  # nosec B404 - fixed local pgrep inspection for Spark executor evidence
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamPublishRequest
from foundry_lite.application.ports.stream_adapter import StreamEvent
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import KafkaStreamAdapter, KafkaStreamAdapterConfig, KafkaStreamSubscription
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.stream_archive import (
    ContinuousStreamArchiveResult,
    StreamArchiveWorkerConfig,
    run_stream_archive_continuously,
    run_stream_archive_once,
)
from testcontainers.kafka import KafkaContainer  # type: ignore[import-untyped]
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "operations" / "local_fault_lab.json"
DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-fault-lab"
DRILL_PASSED = "passed"
DRILL_FAILED = "failed"
DRILL_BOUNDARY = "boundary_not_locally_proven"


@dataclass(frozen=True)
class FaultLabConfig:
    level: str
    output: Path
    storage_root: Path
    kafka_workers: int
    stream_events: int
    stream_limit: int
    spark_rows: int


@dataclass(frozen=True)
class DrillEvidence:
    name: str
    status: str
    details: dict[str, object]


def run_fault_lab(config: FaultLabConfig) -> list[DrillEvidence]:
    drills = [run_spark_executor_loss_drill(config)]
    with _running_kafka_postgres() as infra:
        drills.append(run_kafka_cursor_cas_storm(config, infra))
        drills.append(run_continuous_worker_restart_replay(config, infra))
    drills.append(managed_temporal_failover_boundary())
    drills.append(kafka_consumer_group_rebalance_boundary())
    return drills


def run_spark_executor_loss_drill(config: FaultLabConfig) -> DrillEvidence:
    before = _spark_executor_pids()
    spark = _spark_session()
    killed_pid: int | None = None
    try:
        spark.sparkContext.parallelize([1], 1).count()
        executor_pids = _wait_for_executor_pids(before, expected=2, timeout_seconds=30)
        killed_pid = executor_pids[0]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_spark_stress_job, spark, config.spark_rows)
            time.sleep(0.5)
            os.kill(killed_pid, signal.SIGKILL)
            result = future.result(timeout=120)
        after = _wait_for_executor_pids(before, expected=2, timeout_seconds=30)
        return _passed(
            "spark_local_cluster_executor_loss",
            {
                "sparkMaster": "local-cluster[2,1,512]",
                "rows": config.spark_rows,
                "killedExecutorPid": killed_pid,
                "resultPartitions": result,
                "executorPidsAfterRecovery": after,
            },
        )
    except Exception as exc:  # noqa: BLE001 - operator fault-lab evidence keeps exact failure class
        return _failed("spark_local_cluster_executor_loss", exc, {"killedExecutorPid": killed_pid})
    finally:
        spark.stop()


def run_kafka_cursor_cas_storm(config: FaultLabConfig, infra: KafkaPostgresInfra) -> DrillEvidence:
    topic = f"foundry-lite-fault-storm-{uuid4().hex}"
    consumer_group = f"fault-storm-{uuid4().hex}"
    _publish_live_event(infra.bootstrap_servers, topic, key="S-STORM-0")
    worker_config = _worker_config(config, infra, "raw.fault_storm_events", topic, consumer_group, limit=1)
    _ensure_dataset(config, infra.db_url, worker_config.dataset_ref)
    barrier = Barrier(config.kafka_workers)
    with ThreadPoolExecutor(max_workers=config.kafka_workers) as executor:
        results = list(
            executor.map(
                lambda worker_id: _run_barrier_worker(worker_id, worker_config, barrier),
                range(config.kafka_workers),
            )
        )
    return _cursor_storm_evidence(config, infra.db_url, worker_config.dataset_ref, topic, results)


def run_continuous_worker_restart_replay(config: FaultLabConfig, infra: KafkaPostgresInfra) -> DrillEvidence:
    topic = f"foundry-lite-fault-continuous-{uuid4().hex}"
    consumer_group = f"fault-continuous-{uuid4().hex}"
    for index in range(config.stream_events):
        _publish_live_event(infra.bootstrap_servers, topic, key=f"S-CONT-{index:04d}")
    worker_config = _worker_config(
        config,
        infra,
        "raw.fault_continuous_events",
        topic,
        consumer_group,
        limit=config.stream_limit,
    )
    _ensure_dataset(config, infra.db_url, worker_config.dataset_ref)
    first = run_stream_archive_continuously(
        worker_config,
        should_stop=_stop_after_archived_batches(max(1, _expected_batches(config) // 2)),
    )
    second = run_stream_archive_continuously(worker_config)
    return _continuous_replay_evidence(config, infra.db_url, worker_config.dataset_ref, first, second)


def managed_temporal_failover_boundary() -> DrillEvidence:
    return DrillEvidence(
        name="managed_temporal_cluster_failover",
        status=DRILL_BOUNDARY,
        details={
            "reason": (
                "A laptop time-skipping server can prove workflow replay/error taxonomy, "
                "not managed multi-node failover."
            ),
            "currentLocalProof": "pnpm --silent quality:temporal and quality:temporal-engine-integration",
            "nextProofNeeded": (
                "Temporal dev/Cloud namespace with worker failover, namespace outage, and resume evidence."
            ),
        },
    )


def kafka_consumer_group_rebalance_boundary() -> DrillEvidence:
    return DrillEvidence(
        name="kafka_broker_commit_unknown_rebalance",
        status=DRILL_BOUNDARY,
        details={
            "reason": "The current worker uses assigned offsets plus DB cursor CAS, not broker offset commits.",
            "localProofAdded": "same-offset multi-worker duplicate commit storm is fenced by PostgreSQL cursor CAS.",
            "nextProofNeeded": (
                "Consumer group subscribe/revoke callbacks, broker commit failure injection, and lease fencing."
            ),
        },
    )


@dataclass(frozen=True)
class KafkaPostgresInfra:
    bootstrap_servers: str
    db_url: str
    kafka: object
    postgres: object


@contextmanager
def _running_kafka_postgres() -> Iterator[KafkaPostgresInfra]:
    kafka = KafkaContainer().with_kraft()
    postgres = PostgresContainer("postgres:16-alpine", driver="psycopg")
    kafka.start(timeout=90)
    postgres.start()
    try:
        yield KafkaPostgresInfra(
            bootstrap_servers=kafka.get_bootstrap_server(),
            db_url=_postgres_url(postgres),
            kafka=kafka,
            postgres=postgres,
        )
    finally:
        kafka.stop()
        postgres.stop()


def _spark_session() -> Any:
    from pyspark.sql import SparkSession

    builder = SparkSession.builder
    return (
        builder.master("local-cluster[2,1,512]")
        .appName("foundry-lite-local-fault-lab")
        .config("spark.ui.enabled", "false")
        .config("spark.executor.memory", "512m")
        .config("spark.driver.memory", "768m")
        .config("spark.task.maxFailures", "4")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )


def _run_spark_stress_job(spark: Any, rows: int) -> int:
    context = spark.sparkContext
    return context.parallelize(range(rows), 8).mapPartitions(_slow_partition_sum).count()


def _slow_partition_sum(values: Iterator[int]) -> Iterator[int]:
    total = 0
    for index, value in enumerate(values):
        total += value
        if index % 128 == 0:
            time.sleep(0.002)
    yield total


def _spark_executor_pids() -> set[int]:
    result = subprocess.run(  # nosec B603 B607 - fixed local process inspection command
        ["pgrep", "-f", "org.apache.spark.executor.CoarseGrainedExecutorBackend"],
        check=False,
        capture_output=True,
        text=True,
    )
    return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}


def _wait_for_executor_pids(previous: set[int], *, expected: int, timeout_seconds: float) -> list[int]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = sorted(_spark_executor_pids() - previous)
        if len(current) >= expected:
            return current
        time.sleep(0.25)
    raise RuntimeError(f"expected {expected} Spark executor JVMs")


def _publish_live_event(bootstrap_servers: str, topic: str, *, key: str) -> None:
    adapter = KafkaStreamAdapter(
        KafkaStreamAdapterConfig(
            bootstrap_servers=bootstrap_servers,
            consumer_group=f"fault-producer-{uuid4().hex}",
            subscriptions=(KafkaStreamSubscription("shipments", topic),),
            producer_flush_timeout_seconds=10.0,
        )
    )
    adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipments",
            event_type="shipment.updated",
            tenant_id="tenant-demo",
            request_id=f"req-{key}",
            key=key,
            payload={"shipment_id": key, "status": "IN_TRANSIT"},
        )
    )


def _worker_config(
    config: FaultLabConfig,
    infra: KafkaPostgresInfra,
    dataset_ref: str,
    topic: str,
    consumer_group: str,
    *,
    limit: int,
) -> StreamArchiveWorkerConfig:
    return StreamArchiveWorkerConfig(
        dataset_ref=dataset_ref,
        stream_name="shipments",
        topic=topic,
        bootstrap_servers=infra.bootstrap_servers,
        storage_root=config.storage_root,
        db_url=infra.db_url,
        consumer_group=consumer_group,
        poll_timeout_seconds=1.0,
        max_empty_polls=20,
        limit=limit,
        is_continuous=True,
        continuous_max_empty_polls=1,
    )


def _ensure_dataset(config: FaultLabConfig, db_url: str, dataset_ref: str) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=config.storage_root, db_url=db_url))
    try:
        foundry.datasets.ensure(dataset_ref, ctx=demo_admin_context(), primary_key=["event_id"])
    finally:
        foundry.close()


def _run_barrier_worker(
    worker_id: int,
    config: StreamArchiveWorkerConfig,
    barrier: Barrier,
) -> dict[str, object]:
    try:
        result = run_stream_archive_once(config, stream_adapter=_ReadBarrierKafkaAdapter(config, barrier))
        status = "NO_EVENTS" if result is None else "ARCHIVED"
        return {"worker": worker_id, "status": status, "versionId": getattr(result, "version_id", None)}
    except Exception as exc:  # noqa: BLE001 - a losing worker is expected evidence
        return {"worker": worker_id, "status": "ERROR", "errorType": type(exc).__name__, "error": str(exc)}


class _ReadBarrierKafkaAdapter(KafkaStreamAdapter):
    def __init__(self, config: StreamArchiveWorkerConfig, barrier: Barrier) -> None:
        self._barrier = barrier
        super().__init__(config.kafka_config())

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        events = super().read_events(stream_name, after_offset=after_offset, limit=limit)
        if events:
            self._barrier.wait(timeout=30)
        return events


def _cursor_storm_evidence(
    config: FaultLabConfig,
    db_url: str,
    dataset_ref: str,
    topic: str,
    results: list[dict[str, object]],
) -> DrillEvidence:
    versions = _versions(config, db_url, dataset_ref)
    archived = [result for result in results if result["status"] == "ARCHIVED"]
    conflicts = [result for result in results if result.get("errorType") == "ConflictDetected"]
    is_passed = len(versions) == 1 and len(archived) == 1 and len(conflicts) == config.kafka_workers - 1
    return DrillEvidence(
        name="kafka_postgres_same_offset_cursor_cas_storm",
        status=DRILL_PASSED if is_passed else DRILL_FAILED,
        details={
            "topic": topic,
            "workers": config.kafka_workers,
            "versionCount": len(versions),
            "archivedWorkers": len(archived),
            "conflictWorkers": len(conflicts),
            "results": results,
        },
    )


def _continuous_replay_evidence(
    config: FaultLabConfig,
    db_url: str,
    dataset_ref: str,
    first: ContinuousStreamArchiveResult,
    second: ContinuousStreamArchiveResult,
) -> DrillEvidence:
    rows = _all_dataset_rows(config, db_url, dataset_ref)
    event_ids = [str(row["event_id"]) for row in rows]
    is_passed = len(event_ids) == config.stream_events and len(set(event_ids)) == config.stream_events
    return DrillEvidence(
        name="continuous_worker_restart_replay_overload",
        status=DRILL_PASSED if is_passed else DRILL_FAILED,
        details={
            "publishedEvents": config.stream_events,
            "streamLimit": config.stream_limit,
            "firstRun": asdict(first),
            "secondRun": asdict(second),
            "archivedRows": len(event_ids),
            "uniqueEventIds": len(set(event_ids)),
        },
    )


def _stop_after_archived_batches(limit: int):
    archived_batches = 0

    def should_stop() -> bool:
        nonlocal archived_batches
        if archived_batches >= limit:
            return True
        archived_batches += 1
        return False

    return should_stop


def _expected_batches(config: FaultLabConfig) -> int:
    return (config.stream_events + config.stream_limit - 1) // config.stream_limit


def _versions(config: FaultLabConfig, db_url: str, dataset_ref: str) -> list[dict[str, object]]:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=config.storage_root, db_url=db_url))
    try:
        return [dict(row) for row in foundry.datasets.list_versions(dataset_ref, ctx=demo_admin_context())]
    finally:
        foundry.close()


def _all_dataset_rows(config: FaultLabConfig, db_url: str, dataset_ref: str) -> list[dict[str, object]]:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=config.storage_root, db_url=db_url))
    try:
        rows: list[dict[str, object]] = []
        for version in foundry.datasets.list_versions(dataset_ref, ctx=demo_admin_context()):
            rows.extend(
                dict(row)
                for row in foundry.datasets.preview(
                    dataset_ref,
                    ctx=demo_admin_context(),
                    version=str(version["id"]),
                )
            )
        return rows
    finally:
        foundry.close()


def _postgres_url(container: object) -> str:
    url = container.get_connection_url()  # type: ignore[attr-defined]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _passed(name: str, details: dict[str, object]) -> DrillEvidence:
    return DrillEvidence(name=name, status=DRILL_PASSED, details=details)


def _failed(name: str, exc: Exception, details: dict[str, object] | None = None) -> DrillEvidence:
    return DrillEvidence(
        name=name,
        status=DRILL_FAILED,
        details={"errorType": type(exc).__name__, "error": str(exc), **(details or {})},
    )


def write_evidence(config: FaultLabConfig, drills: list[DrillEvidence]) -> None:
    config.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "passed" if all(drill.status != DRILL_FAILED for drill in drills) else "failed",
        "level": config.level,
        "localOnlyBoundaryCount": sum(1 for drill in drills if drill.status == DRILL_BOUNDARY),
        "failedCount": sum(1 for drill in drills if drill.status == DRILL_FAILED),
        "drills": [asdict(drill) for drill in drills],
    }
    config.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _config_from_args(argv: list[str] | None) -> FaultLabConfig:
    parser = argparse.ArgumentParser(description="Run Foundry-lite local fault lab drills.")
    parser.add_argument("--level", choices=["standard", "stress"], default="standard")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    args = parser.parse_args(argv)
    return _level_config(args.level, args.output, args.storage_root)


def _level_config(level: str, output: Path, storage_root: Path) -> FaultLabConfig:
    if level == "stress":
        return FaultLabConfig(
            level, output, storage_root, kafka_workers=8, stream_events=80, stream_limit=10, spark_rows=20_000
        )
    return FaultLabConfig(
        level,
        output,
        storage_root,
        kafka_workers=4,
        stream_events=24,
        stream_limit=6,
        spark_rows=5_000,
    )


def main(argv: list[str] | None = None) -> int:
    config = _config_from_args(argv)
    config.storage_root.mkdir(parents=True, exist_ok=True)
    drills = run_fault_lab(config)
    write_evidence(config, drills)
    for drill in drills:
        print(f"{drill.status}: {drill.name}")
    print(f"Evidence: {config.output.relative_to(ROOT)}")
    return 1 if any(drill.status == DRILL_FAILED for drill in drills) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
