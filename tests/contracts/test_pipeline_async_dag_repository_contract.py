from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptClaim,
    PipelineNodeRunRecord,
    PipelineRunArtifactRecord,
    PipelineRunEventRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_execution_repository import (
    SqlAlchemyPipelineExecutionRepository,
)
from foundry_lite.infrastructure.repositories.pipeline_repository import (
    SqlAlchemyPipelineRepository,
)
from sqlalchemy import create_engine, insert

TENANT = "tenant-a"
RUN_ID = "pipeline-run-a"
NODE_RUN_ID = "pipeline-node-run-a"
NOW = "2026-07-29T00:00:00Z"


def test_expired_attempt_takeover_fences_stale_worker_writes(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'async-dag-fencing.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        _insert_run(transaction)
        repository.insert_node_run(transaction=transaction, record=_node_record())
        first = repository.claim_node_attempt(
            transaction=transaction,
            claim=_claim("worker-a", "lease-a", NOW, "2026-07-29T00:00:10Z"),
        )
        competing = repository.claim_node_attempt(
            transaction=transaction,
            claim=_claim("worker-b", "lease-b", "2026-07-29T00:00:05Z", "2026-07-29T00:00:35Z"),
        )
        takeover = repository.claim_node_attempt(
            transaction=transaction,
            claim=_claim("worker-b", "lease-b", "2026-07-29T00:00:11Z", "2026-07-29T00:00:41Z"),
        )
        stale_heartbeat = repository.heartbeat_node_attempt(
            transaction=transaction,
            tenant_id=TENANT,
            attempt_id=str(first["id"]),
            worker_id="worker-a",
            lease_token="lease-a",
            fencing_token=int(first["fencing_token"]),
            lease_expires_at="2026-07-29T00:01:00Z",
            heartbeat_at="2026-07-29T00:00:12Z",
        )
        stale_artifact = repository.insert_fenced_artifact(
            transaction=transaction,
            record=_artifact(str(first["id"]), int(first["fencing_token"]), "artifact-stale"),
            worker_id="worker-a",
            lease_token="lease-a",
        )
        current_artifact = repository.insert_fenced_artifact(
            transaction=transaction,
            record=_artifact(str(takeover["id"]), int(takeover["fencing_token"]), "artifact-current"),
            worker_id="worker-b",
            lease_token="lease-b",
        )
        stale_terminal = repository.update_fenced_node_attempt_terminal(
            transaction=transaction,
            tenant_id=TENANT,
            attempt_id=str(first["id"]),
            worker_id="worker-a",
            lease_token="lease-a",
            fencing_token=int(first["fencing_token"]),
            status="SUCCEEDED",
            output_manifest={},
            error=None,
            error_kind=None,
            completed_at="2026-07-29T00:00:12Z",
        )

    assert first is not None and first["fencing_token"] == 1
    assert competing is None
    assert takeover is not None and takeover["fencing_token"] == 2
    assert takeover["attempt_number"] == 2
    assert stale_heartbeat is None
    assert stale_artifact is None
    assert stale_terminal is None
    assert current_artifact is not None


def test_run_event_sequence_is_monotonic_and_resumable(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'async-dag-events.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        _insert_run(transaction)
        first = repository.append_run_event(
            transaction=transaction,
            record=_event("event-a", "pipeline.run.queued"),
        )
        second = repository.append_run_event(
            transaction=transaction,
            record=_event("event-b", "pipeline.run.running"),
        )
        resumed = repository.run_events(
            transaction=transaction,
            tenant_id=TENANT,
            run_id=RUN_ID,
            after_sequence=1,
            limit=100,
        )

    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert [(event["sequence"], event["event_type"]) for event in resumed] == [(2, "pipeline.run.running")]


def test_expired_lease_cannot_commit_before_takeover(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'async-dag-expired.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        _insert_run(transaction)
        repository.insert_node_run(transaction=transaction, record=_node_record())
        attempt = repository.claim_node_attempt(
            transaction=transaction,
            claim=_claim("worker-a", "lease-a", NOW, "2026-07-29T00:00:10Z"),
        )
        expired = _artifact(str(attempt["id"]), int(attempt["fencing_token"]), "expired")
        expired = replace(expired, created_at="2026-07-29T00:00:11Z")
        artifact = repository.insert_fenced_artifact(
            transaction=transaction,
            record=expired,
            worker_id="worker-a",
            lease_token="lease-a",
        )

    assert artifact is None


def test_active_attempt_cancellation_is_owner_scoped_and_fenced(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'async-dag-cancel.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        _insert_run(transaction)
        repository.insert_node_run(transaction=transaction, record=_node_record())
        attempt = repository.claim_node_attempt(
            transaction=transaction,
            claim=_claim("worker-a", "lease-a", NOW, "2026-07-29T00:00:30Z"),
        )
        wrong_owner = repository.cancel_active_node_attempt(
            transaction=transaction,
            tenant_id=TENANT,
            run_id=RUN_ID,
            node_id="node-a",
            worker_id="worker-b",
            external_execution_id="activity-a",
            completed_at="2026-07-29T00:00:01Z",
        )
        cancelled = repository.cancel_active_node_attempt(
            transaction=transaction,
            tenant_id=TENANT,
            run_id=RUN_ID,
            node_id="node-a",
            worker_id="worker-a",
            external_execution_id="activity-a",
            completed_at="2026-07-29T00:00:02Z",
        )
        stale_artifact = repository.insert_fenced_artifact(
            transaction=transaction,
            record=_artifact(str(attempt["id"]), int(attempt["fencing_token"]), "after-cancel"),
            worker_id="worker-a",
            lease_token="lease-a",
        )
        node = repository.node_run_by_run_node(
            transaction=transaction,
            tenant_id=TENANT,
            run_id=RUN_ID,
            node_id="node-a",
        )

    assert wrong_owner is None
    assert cancelled is not None and cancelled["status"] == "CANCELLED"
    assert node is not None and node["status"] == "CANCELLED"
    assert stale_artifact is None


def test_schedule_terminal_observation_claim_is_exactly_once(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'async-dag-terminal-observer.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)
    with engine.begin() as transaction:
        transaction.execute(
            insert(db.pipeline_runs).values(
                id=RUN_ID,
                tenant_id=TENANT,
                pipeline_id="pipeline-a",
                version_id="pipeline-version-a",
                status="failed",
                schedule_id="schedule-a",
                schedule_slot_at=NOW,
                timeline=[],
                error={"code": "PERMANENT_FAILURE"},
                created_by="pipeline-scheduler",
                started_at=NOW,
                completed_at="2026-07-29T00:01:00Z",
            )
        )
        pending = repository.unobserved_terminal_schedule_runs(
            transaction=transaction,
            tenant_id=TENANT,
            limit=10,
        )
        first = repository.claim_terminal_schedule_observation(
            transaction=transaction,
            tenant_id=TENANT,
            run_id=RUN_ID,
            observed_at="2026-07-29T00:02:00Z",
        )
        duplicate = repository.claim_terminal_schedule_observation(
            transaction=transaction,
            tenant_id=TENANT,
            run_id=RUN_ID,
            observed_at="2026-07-29T00:03:00Z",
        )
        remaining = repository.unobserved_terminal_schedule_runs(
            transaction=transaction,
            tenant_id=TENANT,
            limit=10,
        )

    assert [row["id"] for row in pending] == [RUN_ID]
    assert first is not None and first["terminal_observed_at"] == "2026-07-29T00:02:00Z"
    assert duplicate is None
    assert remaining == []


def _insert_run(transaction: object) -> None:
    transaction.execute(
        insert(db.pipeline_runs).values(
            id=RUN_ID,
            tenant_id=TENANT,
            pipeline_id="pipeline-a",
            version_id="pipeline-version-a",
            status="running",
            timeline=[],
            created_by="user-a",
            started_at=NOW,
        )
    )


def _node_record() -> PipelineNodeRunRecord:
    return PipelineNodeRunRecord(
        node_run_id=NODE_RUN_ID,
        tenant_id=TENANT,
        run_id=RUN_ID,
        node_id="node-a",
        descriptor_id="dataset.input",
        spec_version="1.0.0",
        input_artifacts=[],
        created_at=NOW,
    )


def _claim(worker: str, lease: str, heartbeat: str, expires: str) -> PipelineNodeAttemptClaim:
    return PipelineNodeAttemptClaim(
        tenant_id=TENANT,
        node_run_id=NODE_RUN_ID,
        worker_id=worker,
        lease_token=lease,
        lease_expires_at=expires,
        heartbeat_at=heartbeat,
        executor_profile="local",
        input_manifest={},
        external_execution_id="activity-a",
    )


def _artifact(attempt_id: str, fence: int, suffix: str) -> PipelineRunArtifactRecord:
    return PipelineRunArtifactRecord(
        artifact_id=suffix,
        tenant_id=TENANT,
        run_id=RUN_ID,
        node_run_id=NODE_RUN_ID,
        attempt_id=attempt_id,
        fencing_token=fence,
        node_id="node-a",
        port_id="output",
        artifact_kind="dataset_version",
        plane="data",
        artifact_ref={"datasetRid": "dataset-a"},
        manifest={},
        content_fingerprint=suffix,
        security_envelope={},
        status="COMMITTED",
        is_serving=True,
        idempotency_key=suffix,
        committed_at=NOW,
        created_at=NOW,
    )


def _event(event_id: str, event_type: str) -> PipelineRunEventRecord:
    return PipelineRunEventRecord(
        event_id=event_id,
        tenant_id=TENANT,
        run_id=RUN_ID,
        event_type=event_type,
        payload={},
        created_at=NOW,
    )
