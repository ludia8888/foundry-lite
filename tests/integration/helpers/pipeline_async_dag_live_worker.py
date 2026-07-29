"""Real Temporal worker used by the Pipeline async DAG live fault tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from foundry_lite.application.pipeline_async_execution_types import (
    PipelineNodeAttemptClaim,
    PipelineNodeAttemptRow,
    PipelineRunArtifactRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.temporal_workflows import (
    PIPELINE_DAG_TASK_QUEUE,
    PipelineDagActivities,
    PipelineDagWorkflow,
    foundry_sandbox_runner,
)
from foundry_lite.infrastructure.repositories.pipeline_execution_repository import (
    SqlAlchemyPipelineExecutionRepository,
)
from sqlalchemy import create_engine, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio.client import Client
from temporalio.worker import Worker

_RESULT_PREFIX = "FOUNDRY_LITE_PIPELINE_RESULT="


async def run_worker() -> None:
    client = await Client.connect(os.environ["FOUNDRY_LITE_TEMPORAL_ADDRESS"])
    worker_id = os.environ["FOUNDRY_LITE_WORKER_ID"]
    driver = LivePipelineDriver(os.environ["FOUNDRY_LITE_DB_URL"], worker_id)
    activities = PipelineDagActivities(
        driver.drive,
        worker_id=worker_id,
        node_subprocess_argv=(sys.executable, str(Path(__file__).resolve()), "node"),
        termination_grace_seconds=1,
    )
    worker = Worker(
        client,
        task_queue=os.getenv("FOUNDRY_LITE_PIPELINE_DAG_TASK_QUEUE", PIPELINE_DAG_TASK_QUEUE),
        workflows=[PipelineDagWorkflow],
        activities=[
            activities.begin,
            activities.execute_node,
            activities.finalize,
            activities.execute_preview,
        ],
        workflow_runner=foundry_sandbox_runner(),
    )
    await worker.run()


class LivePipelineDriver:
    """Small real-DB driver focused on lease/fence and immutable commit proof."""

    def __init__(self, db_url: str, worker_id: str) -> None:
        self._engine = create_engine(db_url, future=True)
        self._repository = SqlAlchemyPipelineExecutionRepository(self._engine)
        self._worker_id = worker_id

    def drive(self, payload: dict[str, object]) -> dict[str, object]:
        operation = str(payload["operation"])
        if operation == "begin":
            return self._begin(payload)
        if operation == "execute_node":
            return self._execute_node(payload)
        if operation == "cancel_node":
            return self._cancel_node(payload)
        if operation == "finalize":
            return self._finalize(payload)
        raise RuntimeError(f"unsupported live operation: {operation}")

    def _begin(self, payload: dict[str, object]) -> dict[str, object]:
        with self._engine.begin() as transaction:
            transaction.execute(
                update(db.pipeline_runs).where(db.pipeline_runs.c.id == payload["run_id"]).values(status="running")
            )
        return {"runId": payload["run_id"], "status": "running"}

    def _execute_node(self, payload: dict[str, object]) -> dict[str, object]:
        node = _node(payload)
        now = _now()
        with self._engine.begin() as transaction:
            attempt = self._repository.claim_node_attempt(
                transaction=transaction,
                claim=PipelineNodeAttemptClaim(
                    tenant_id=str(payload["tenant_id"]),
                    node_run_id=f"{payload['run_id']}:node:{node['nodeId']}",
                    worker_id=str(payload["workerIdentity"]),
                    lease_token=f"{payload['externalExecutionId']}:{payload['temporalAttempt']}",
                    lease_expires_at=_after(now, 8),
                    heartbeat_at=now,
                    executor_profile="live-temporal",
                    input_manifest={},
                    external_execution_id=str(payload["externalExecutionId"]),
                ),
            )
        if attempt is None:
            raise RuntimeError("live node attempt is currently owned")
        _block_first_attempt(node, attempt, payload)
        return self._commit_node(payload, node, attempt)

    def _commit_node(
        self,
        payload: dict[str, object],
        node: dict[str, object],
        attempt: PipelineNodeAttemptRow,
    ) -> dict[str, object]:
        now = _now()
        with self._engine.begin() as transaction:
            _insert_domain_commit(transaction, payload, node, now)
            artifact = self._repository.insert_fenced_artifact(
                transaction=transaction,
                record=_artifact_record(payload, node, attempt, now),
                worker_id=str(attempt["lease_owner"]),
                lease_token=str(attempt["lease_token"]),
            )
            if artifact is None:
                raise RuntimeError("expired or stale live worker commit was fenced")
            completed = self._repository.update_fenced_node_attempt_terminal(
                transaction=transaction,
                tenant_id=str(payload["tenant_id"]),
                attempt_id=str(attempt["id"]),
                worker_id=str(attempt["lease_owner"]),
                lease_token=str(attempt["lease_token"]),
                fencing_token=attempt["fencing_token"],
                status="SUCCEEDED",
                output_manifest={"artifactId": artifact["id"]},
                error=None,
                error_kind=None,
                completed_at=now,
            )
            if completed is None:
                raise RuntimeError("live attempt terminal write was fenced")
        return {
            "nodeId": node["nodeId"],
            "status": "succeeded",
            "outputs": [{"plane": node["plane"], "artifactId": artifact["id"]}],
        }

    def _cancel_node(self, payload: dict[str, object]) -> dict[str, object]:
        node = _node(payload)
        completed_at = _now()
        with self._engine.begin() as transaction:
            attempt = self._repository.cancel_active_node_attempt(
                transaction=transaction,
                tenant_id=str(payload["tenant_id"]),
                run_id=str(payload["run_id"]),
                node_id=str(node["nodeId"]),
                worker_id=str(payload["workerIdentity"]),
                external_execution_id=str(payload["externalExecutionId"]),
                completed_at=completed_at,
            )
            self._repository.cancel_inactive_node_runs(
                transaction=transaction,
                tenant_id=str(payload["tenant_id"]),
                run_id=str(payload["run_id"]),
                completed_at=completed_at,
            )
            transaction.execute(
                update(db.pipeline_runs)
                .where(
                    db.pipeline_runs.c.id == payload["run_id"],
                    db.pipeline_runs.c.status == "cancelling",
                )
                .values(status="cancelled", completed_at=completed_at)
            )
        return {"nodeId": node["nodeId"], "status": "cancelled", "attempt": bool(attempt)}

    def _finalize(self, payload: dict[str, object]) -> dict[str, object]:
        with self._engine.begin() as transaction:
            transaction.execute(
                update(db.pipeline_runs)
                .where(db.pipeline_runs.c.id == payload["run_id"])
                .values(status="succeeded", completed_at=_now())
            )
        return {"runId": payload["run_id"], "status": "succeeded"}


def _block_first_attempt(
    node: dict[str, object],
    attempt: PipelineNodeAttemptRow,
    payload: dict[str, object],
) -> None:
    if node.get("blockFirstAttempt") is not True or attempt["attempt_number"] != 1:
        return
    marker = Path(os.environ["FOUNDRY_LITE_LIVE_MARKER_DIR"])
    marker.mkdir(parents=True, exist_ok=True)
    path = marker / f"{payload['run_id']}-{node['nodeId']}.json"
    path.write_text(
        json.dumps({"pid": os.getpid(), "workerId": payload["workerIdentity"]}),
        encoding="utf-8",
    )
    Event().wait(12)


def _insert_domain_commit(
    transaction: Any,
    payload: dict[str, object],
    node: dict[str, object],
    now: str,
) -> None:
    run_id = str(payload["run_id"])
    if node["plane"] == "data":
        transaction.execute(
            pg_insert(db.dataset_transactions)
            .values(
                id=f"{run_id}:dataset-tx",
                tenant_id=payload["tenant_id"],
                dataset_id="live-dataset",
                branch="main",
                tx_type="APPEND",
                status="COMMITTED",
                committed_version_id=f"{run_id}:dataset-version",
                created_at=now,
                committed_at=now,
                metadata={"pipelineRunId": run_id},
            )
            .on_conflict_do_nothing()
        )
        transaction.execute(
            pg_insert(db.dataset_versions)
            .values(
                id=f"{run_id}:dataset-version",
                tenant_id=payload["tenant_id"],
                dataset_id="live-dataset",
                branch="main",
                version_number=_version_number(run_id),
                transaction_id=f"{run_id}:dataset-tx",
                schema_version=1,
                manifest_uri=f"live://{run_id}/dataset",
                row_count=1,
                byte_size=1,
                status="COMMITTED",
                created_at=now,
            )
            .on_conflict_do_nothing()
        )
        return
    transaction.execute(
        pg_insert(db.media_transactions)
        .values(
            id=f"{run_id}:media-tx",
            tenant_id=payload["tenant_id"],
            media_set_id="live-media-set",
            status="COMMITTED",
            mode="append",
            idempotency_key=f"{run_id}:media",
            request_fingerprint=run_id,
            opened_at=now,
            committed_at=now,
            trace={"pipelineRunId": run_id},
        )
        .on_conflict_do_nothing()
    )
    transaction.execute(
        pg_insert(db.media_item_versions)
        .values(
            id=f"{run_id}:media-version",
            tenant_id=payload["tenant_id"],
            media_item_id="live-media-item",
            media_transaction_id=f"{run_id}:media-tx",
            version_number=_version_number(run_id),
            blob_key=f"live/{run_id}",
            content_hash=run_id,
            byte_size=1,
            supplied_mime_type="image/png",
            sniffed_mime_type="image/png",
            schema_type="image",
            format="png",
            probe_metadata={},
            security_envelope={"classification": "internal"},
            status="COMMITTED",
            created_at=now,
            committed_at=now,
        )
        .on_conflict_do_nothing()
    )


def _artifact_record(
    payload: dict[str, object],
    node: dict[str, object],
    attempt: PipelineNodeAttemptRow,
    now: str,
) -> PipelineRunArtifactRecord:
    run_id = str(payload["run_id"])
    node_id = str(node["nodeId"])
    return PipelineRunArtifactRecord(
        artifact_id=f"{run_id}:artifact:{node_id}",
        tenant_id=str(payload["tenant_id"]),
        run_id=run_id,
        node_run_id=f"{run_id}:node:{node_id}",
        attempt_id=str(attempt["id"]),
        fencing_token=attempt["fencing_token"],
        node_id=node_id,
        port_id="output",
        artifact_kind=f"{node['plane']}_version",
        plane=str(node["plane"]),
        artifact_ref={"runId": run_id, "plane": node["plane"]},
        manifest={},
        content_fingerprint=f"{run_id}:{node_id}",
        security_envelope={"classification": "internal"},
        status="COMMITTED",
        is_serving=True,
        idempotency_key=f"{run_id}:{node_id}:serving",
        committed_at=now,
        created_at=now,
    )


def _node(payload: dict[str, object]) -> dict[str, object]:
    value = payload["node"]
    if not isinstance(value, dict):
        raise TypeError("live node payload is invalid")
    return value


def _version_number(run_id: str) -> int:
    return int.from_bytes(run_id.encode()[-4:], "big") % 2_000_000_000 + 1


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _after(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def node_main() -> None:
    payload = json.load(sys.stdin)
    driver = LivePipelineDriver(
        os.environ["FOUNDRY_LITE_DB_URL"],
        str(payload["workerIdentity"]),
    )
    result = driver.drive(payload)
    sys.stdout.write(f"{_RESULT_PREFIX}{json.dumps(result, separators=(',', ':'))}\n")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "node":
        node_main()
        return
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
