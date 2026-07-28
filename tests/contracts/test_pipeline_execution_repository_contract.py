from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineDeploymentRecord,
    PipelineNodeAttemptRecord,
    PipelineNodeRunRecord,
    PipelinePreviewRunRecord,
    PipelineRunArtifactRecord,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_NODE_ATTEMPT_SUCCEEDED,
    PIPELINE_NODE_RUN_SUCCEEDED,
    PIPELINE_PREVIEW_FAILED,
    PIPELINE_PREVIEW_SUCCEEDED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_execution_repository import (
    SqlAlchemyPipelineExecutionRepository,
)
from sqlalchemy import create_engine

NOW = "2026-07-16T00:00:00+00:00"


def test_preview_run_contract_is_tenant_scoped_and_commit_forbidden(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-preview.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        created = repository.insert_preview(transaction=transaction, record=_preview_record())
        replay = repository.preview_by_idempotency_key(
            transaction=transaction,
            tenant_id="tenant-a",
            idempotency_key="preview-key",
        )
        hidden = repository.preview_by_id(
            transaction=transaction,
            tenant_id="tenant-b",
            preview_run_id="preview-a",
        )
        claimed = repository.claim_preview(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            started_at=NOW,
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-16T00:02:00+00:00",
            execution_heartbeat_at=NOW,
        )
        completed = repository.update_preview_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            transition=PIPELINE_PREVIEW_SUCCEEDED,
            outputs=[{"kind": "dataset_version", "rows": [{"id": 1}]}],
            artifacts=[],
            error=None,
            completed_at=NOW,
            execution_lease_token="lease-a",
        )
    assert created["status"] == "QUEUED"
    assert created["is_commit_forbidden"] is True
    assert replay is not None and replay["id"] == "preview-a"
    assert hidden is None
    assert claimed is not None and claimed["status"] == "RUNNING"
    assert completed is not None and completed["status"] == "SUCCEEDED"
    assert completed["outputs"][0]["rows"] == [{"id": 1}]


def test_preview_recovery_query_is_explicitly_tenant_scoped_on_sqlite(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-preview-recovery-tenants.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_preview(
            transaction=transaction,
            record=_preview_record(preview_run_id="preview-a", tenant_id="tenant-a", idempotency_key="key-a"),
        )
        repository.insert_preview(
            transaction=transaction,
            record=_preview_record(preview_run_id="preview-b", tenant_id="tenant-b", idempotency_key="key-b"),
        )
        tenant_b_rows = repository.recoverable_previews(
            transaction=transaction,
            tenant_id="tenant-b",
            as_of=NOW,
            limit=10,
        )

    assert [(row["tenant_id"], row["id"]) for row in tenant_b_rows] == [("tenant-b", "preview-b")]


def test_preview_success_atomically_honors_concurrent_cancellation(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-preview-cancel.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_preview(transaction=transaction, record=_preview_record())
        repository.claim_preview(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            started_at=NOW,
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-16T00:02:00+00:00",
            execution_heartbeat_at=NOW,
        )
        repository.request_preview_cancel(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            requested_at=NOW,
        )
        completed = repository.complete_preview_success(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            execution_lease_token="lease-a",
            outputs=[{"kind": "table"}],
            artifacts=[],
            completed_at=NOW,
        )

    assert completed is not None
    assert completed["status"] == "CANCELLED"


def test_preview_failure_atomically_honors_concurrent_cancellation(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-preview-cancelled-failure.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_preview(transaction=transaction, record=_preview_record())
        repository.claim_preview(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            started_at=NOW,
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-16T00:02:00+00:00",
            execution_heartbeat_at=NOW,
        )
        repository.request_preview_cancel(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            requested_at=NOW,
        )
        completed = repository.complete_preview_failure(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            execution_lease_token="lease-a",
            error={"code": "NODE_FAILED"},
            completed_at=NOW,
        )

    assert completed is not None
    assert completed["status"] == "CANCELLED"
    assert completed["error"] is None


def test_preview_failure_records_error_when_no_cancel_won(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-preview-failure.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_preview(transaction=transaction, record=_preview_record())
        repository.claim_preview(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            started_at=NOW,
            execution_lease_token="lease-a",
            execution_lease_expires_at="2026-07-16T00:02:00+00:00",
            execution_heartbeat_at=NOW,
        )
        completed = repository.complete_preview_failure(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            execution_lease_token="lease-a",
            error={"code": "NODE_FAILED"},
            completed_at=NOW,
        )

    assert completed is not None
    assert completed["status"] == "FAILED"
    assert completed["error"] == {"code": "NODE_FAILED"}


def test_expired_preview_reclaim_fences_the_original_executor(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-preview-reclaim.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_preview(transaction=transaction, record=_preview_record())
        repository.claim_preview(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            started_at="2026-07-15T23:57:00+00:00",
            execution_lease_token="expired-lease",
            execution_lease_expires_at="2026-07-15T23:59:00+00:00",
            execution_heartbeat_at="2026-07-15T23:57:00+00:00",
        )
        recoverable = repository.recoverable_previews(
            transaction=transaction,
            tenant_id="tenant-a",
            as_of=NOW,
            limit=10,
        )
        reclaimed = repository.reclaim_expired_preview(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            reclaim_before=NOW,
            execution_lease_token="recovery-lease",
            execution_lease_expires_at="2026-07-16T00:02:00+00:00",
            execution_heartbeat_at=NOW,
        )
        stale_terminal = repository.update_preview_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            transition=PIPELINE_PREVIEW_FAILED,
            outputs=[],
            artifacts=[],
            error={"code": "stale"},
            completed_at=NOW,
            execution_lease_token="expired-lease",
        )
        completed = repository.complete_preview_success(
            transaction=transaction,
            tenant_id="tenant-a",
            preview_run_id="preview-a",
            execution_lease_token="recovery-lease",
            outputs=[{"kind": "table"}],
            artifacts=[],
            completed_at=NOW,
        )

    assert [row["id"] for row in recoverable] == ["preview-a"]
    assert reclaimed is not None and reclaimed["execution_lease_token"] == "recovery-lease"
    assert stale_terminal is None
    assert completed is not None and completed["status"] == "SUCCEEDED"


def test_node_attempt_and_artifact_contract_preserves_passport(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-artifact.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        node = repository.insert_node_run(transaction=transaction, record=_node_record())
        node_replay = repository.insert_node_run(
            transaction=transaction,
            record=_node_record(node_run_id="node-run-replay"),
        )
        running = repository.claim_node_run(
            transaction=transaction,
            tenant_id="tenant-a",
            node_run_id="node-run-a",
            attempt_number=1,
            input_artifacts=[{"artifactId": "source-artifact", "versionId": "dataset-version-1"}],
            started_at=NOW,
            updated_at=NOW,
        )
        attempt = repository.insert_node_attempt(transaction=transaction, record=_attempt_record())
        attempt_replay = repository.insert_node_attempt(
            transaction=transaction,
            record=_attempt_record(attempt_id="attempt-replay"),
        )
        artifact = repository.insert_artifact(transaction=transaction, record=_artifact_record())
        artifact_replay = repository.insert_artifact(
            transaction=transaction,
            record=_artifact_record(artifact_id="artifact-replay"),
        )
        completed_attempt = repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            attempt_id="attempt-a",
            transition=PIPELINE_NODE_ATTEMPT_SUCCEEDED,
            output_manifest={"artifactIds": ["artifact-a"]},
            error=None,
            completed_at=NOW,
        )
        completed_node = repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id="tenant-a",
            node_run_id="node-run-a",
            transition=PIPELINE_NODE_RUN_SUCCEEDED,
            output_artifacts=[{"artifactId": "artifact-a"}],
            error=None,
            completed_at=NOW,
            updated_at=NOW,
        )
        nodes = repository.node_runs_for_run(transaction=transaction, tenant_id="tenant-a", run_id="run-a")
        attempts = repository.attempts_for_node_run(
            transaction=transaction,
            tenant_id="tenant-a",
            node_run_id="node-run-a",
        )
        artifacts = repository.artifacts_for_run(transaction=transaction, tenant_id="tenant-a", run_id="run-a")
    assert node["descriptor_id"] == "document.extract"
    assert node_replay["id"] == node["id"]
    assert running is not None and running["status"] == "RUNNING"
    assert running["attempt_count"] == 1
    assert attempt["executor_profile"] == "local-media"
    assert attempt_replay["id"] == attempt["id"]
    assert artifact["artifact_kind"] == "content_unit_set"
    assert artifact_replay["id"] == artifact["id"]
    assert artifact["is_serving"] is False
    assert artifacts[0]["manifest"]["processorVersion"] == "1.0.0"
    assert completed_attempt is not None and completed_attempt["status"] == "SUCCEEDED"
    assert completed_node is not None and completed_node["status"] == "SUCCEEDED"
    assert nodes == [completed_node]
    assert attempts == [completed_attempt]


def test_deployment_contract_assigns_sequence_and_replays_idempotently(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-deployment.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        first = repository.insert_deployment(transaction=transaction, record=_deployment_record("pdep-a", "deploy-a"))
        replay = repository.insert_deployment(
            transaction=transaction,
            record=_deployment_record("pdep-replay", "deploy-a"),
        )
        second = repository.insert_deployment(transaction=transaction, record=_deployment_record("pdep-b", "deploy-b"))
        listed = repository.list_deployments(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipeline-a",
            limit=10,
        )
    assert first["deployment_number"] == 1
    assert replay["id"] == first["id"]
    assert second["deployment_number"] == 2
    assert [row["id"] for row in listed] == ["pdep-b", "pdep-a"]
    assert listed[0]["execution_plan"]["planFingerprint"] == "plan-fp"


def test_promoted_deployment_lookup_is_not_limited_to_latest_hundred(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-deployment-history.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        target = repository.insert_deployment(
            transaction=transaction,
            record=_deployment_record(
                "pdep-target",
                "deploy-target",
                version_id="version-target",
                plan_fingerprint="plan-target",
            ),
        )
        for index in range(101):
            repository.insert_deployment(
                transaction=transaction,
                record=_deployment_record(
                    f"pdep-newer-{index}",
                    f"deploy-newer-{index}",
                    version_id=f"version-newer-{index}",
                    plan_fingerprint=f"plan-newer-{index}",
                ),
            )
        latest_hundred = repository.list_deployments(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipeline-a",
            limit=100,
        )
        resolved = repository.promoted_deployment_for_version(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipeline-a",
            version_id="version-target",
            plan_fingerprint="plan-target",
        )
        wrong_plan = repository.promoted_deployment_for_version(
            transaction=transaction,
            tenant_id="tenant-a",
            pipeline_id="pipeline-a",
            version_id="version-target",
            plan_fingerprint="plan-other",
        )
        wrong_tenant = repository.promoted_deployment_for_version(
            transaction=transaction,
            tenant_id="tenant-b",
            pipeline_id="pipeline-a",
            version_id="version-target",
            plan_fingerprint="plan-target",
        )

    assert target["id"] not in {row["id"] for row in latest_hundred}
    assert resolved is not None
    assert resolved["id"] == target["id"]
    assert wrong_plan is None
    assert wrong_tenant is None


def test_deployment_contract_retries_a_concurrent_number_collision(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-deployment-race.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_deployment(transaction=transaction, record=_deployment_record("pdep-a", "deploy-a"))
        real_next = repository._next_deployment_number
        calls = 0

        def stale_then_current(transaction, tenant_id: str, pipeline_id: str) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return 1
            return real_next(transaction, tenant_id, pipeline_id)

        monkeypatch.setattr(repository, "_next_deployment_number", stale_then_current)
        second = repository.insert_deployment(
            transaction=transaction,
            record=_deployment_record("pdep-b", "deploy-b"),
        )

    assert calls == 2
    assert second["deployment_number"] == 2


def _preview_record(
    *,
    preview_run_id: str = "preview-a",
    tenant_id: str = "tenant-a",
    idempotency_key: str = "preview-key",
) -> PipelinePreviewRunRecord:
    return PipelinePreviewRunRecord(
        preview_run_id=preview_run_id,
        tenant_id=tenant_id,
        pipeline_id="pipeline-a",
        branch_id="branch-a",
        graph={"schemaVersion": 2, "nodes": [], "edges": []},
        graph_fingerprint="graph-fp",
        target_node_id=None,
        limits={"tableRows": 50, "maxBytes": 33_554_432},
        idempotency_key=idempotency_key,
        request_fingerprint="request-fp",
        execution_context={
            "actorUserId": "user-a",
            "roles": ["data_engineer"],
            "applicationId": None,
            "clientId": None,
            "tokenScopes": [],
        },
        created_by="user-a",
        created_at=NOW,
    )


def _node_record(*, node_run_id: str = "node-run-a") -> PipelineNodeRunRecord:
    return PipelineNodeRunRecord(
        node_run_id=node_run_id,
        tenant_id="tenant-a",
        run_id="run-a",
        node_id="extract-a",
        descriptor_id="document.extract",
        spec_version="1.0.0",
        input_artifacts=[{"kind": "media_set_selection", "version": "v1"}],
        created_at=NOW,
    )


def _attempt_record(*, attempt_id: str = "attempt-a") -> PipelineNodeAttemptRecord:
    return PipelineNodeAttemptRecord(
        attempt_id=attempt_id,
        tenant_id="tenant-a",
        node_run_id="node-run-a",
        attempt_number=1,
        executor_profile="local-media",
        input_manifest={"securityEnvelope": {"classification": "CONFIDENTIAL"}},
        started_at=NOW,
    )


def _artifact_record(*, artifact_id: str = "artifact-a") -> PipelineRunArtifactRecord:
    return PipelineRunArtifactRecord(
        artifact_id=artifact_id,
        tenant_id="tenant-a",
        run_id="run-a",
        node_run_id="node-run-a",
        node_id="extract-a",
        port_id="content",
        artifact_kind="content_unit_set",
        plane="content",
        artifact_ref={"previewRunId": "preview-a"},
        manifest={"processorName": "tesseract", "processorVersion": "1.0.0"},
        content_fingerprint="content-fp",
        security_envelope={"classification": "CONFIDENTIAL"},
        status="STAGED",
        is_serving=False,
        idempotency_key="artifact-key",
        committed_at=None,
        created_at=NOW,
    )


def _deployment_record(
    deployment_id: str,
    idempotency_key: str,
    *,
    version_id: str = "version-a",
    plan_fingerprint: str = "plan-fp",
) -> PipelineDeploymentRecord:
    return PipelineDeploymentRecord(
        deployment_id=deployment_id,
        tenant_id="tenant-a",
        pipeline_id="pipeline-a",
        version_id=version_id,
        status="PROMOTED",
        execution_plan={"compilerVersion": "pipeline-plan-v2.0", "planFingerprint": plan_fingerprint},
        plan_fingerprint=plan_fingerprint,
        compiler_version="pipeline-plan-v2.0",
        processor_pins=[],
        model_pins=[],
        function_pins=[],
        compute_profile={},
        idempotency_key=idempotency_key,
        request_fingerprint="request-fp",
        promoted_by="user-a",
        promoted_at=NOW,
        rolled_back_from_id=None,
        created_by="user-a",
        created_at=NOW,
    )
