from __future__ import annotations

from pathlib import Path
from threading import Event, current_thread
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagDispatchRequest,
    UnavailablePipelineDagOrchestrator,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptClaim,
    PipelineNodeRunRecord,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRunRow
from foundry_lite.application.services import pipeline_run_recovery
from foundry_lite.application.services.pipeline_async_run_service import PipelineAsyncRunService
from foundry_lite.application.services.pipeline_distributed_node_service import (
    PipelineDistributedNodeService,
    PipelineNodeRetryableFailure,
)
from foundry_lite.application.services.pipeline_run_recovery import PipelineExecutionLeaseLost
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RunResult,
    PipelineV2RuntimeArtifact,
    pipeline_runtime_artifact_payload,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import insert

NOW = "2026-07-29T00:00:00Z"


def test_async_run_unknown_dispatch_history_cancel_and_control_recovery(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    ctx = demo_admin_context()
    _insert_version(foundry, "version-async", _plan())
    service = foundry._services.pipelines.async_run
    service.pipeline_dag_orchestrator = UnavailablePipelineDagOrchestrator()

    first = _enqueue(service, ctx, "async-a")
    replay = _enqueue(service, ctx, "async-a")
    second = _enqueue(service, ctx, "async-b")
    page = service.list_runs("pipeline-a", cursor=None, limit=1, ctx=ctx)
    next_page = service.list_runs("pipeline-a", cursor=cast(str, page["nextCursor"]), limit=1, ctx=ctx)
    events = service.events(str(first["id"]), after_sequence=-1, limit=999, ctx=ctx)
    recovered = service.recover_dispatches(tenant_id="tenant-demo", limit=999)
    cancelling = service.cancel(str(first["id"]), idempotency_key="cancel-a", reason="stop", ctx=ctx)
    control = foundry._services.pipelines.control.tick(limit=999)
    terminal = service.pipeline_run_service.get_run(str(first["id"]), ctx=ctx)

    assert first["id"] == replay["id"]
    assert first["orchestration"]["dispatchStatus"] == "unknown"
    assert second["id"] != first["id"]
    assert page["nextCursor"] is not None
    assert next_page["items"]
    assert [event["id"] for event in events["events"]] == sorted(event["id"] for event in events["events"])
    assert recovered["recovered"] >= 2
    assert cancelling["status"] == "cancelling"
    assert control["cancellations"] == 1
    assert terminal["status"] == "cancelled"
    assert service.cancel(str(first["id"]), idempotency_key="cancel-a", reason="stop", ctx=ctx)["status"] == "cancelled"

    with pytest.raises(ValidationFailed, match="Idempotency-Key"):
        service.cancel(str(second["id"]), idempotency_key=" ", reason=None, ctx=ctx)
    with pytest.raises(ValidationFailed, match="waitSeconds"):
        _enqueue(service, ctx, "invalid-wait", wait_seconds=31)
    with pytest.raises(ValidationFailed, match="cursor is invalid"):
        service.list_runs("pipeline-a", cursor="not-base64", limit=10, ctx=ctx)


def test_async_dispatched_driver_handles_success_cancellation_and_lease_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry(tmp_path)
    _insert_version(foundry, "version-dispatched", _plan())
    _insert_run(foundry, "run-dispatched", "version-dispatched", status="queued")
    _insert_run(foundry, "run-dispatched-cancel", "version-dispatched", status="cancelling")
    service = foundry._services.pipelines.async_run
    calls: list[str] = []

    monkeypatch.setattr(
        service.pipeline_run_service,
        "execute_queued_run",
        lambda _ctx, row, _version: calls.append(str(row["id"])),
    )
    service.execute_dispatched(_dispatch_request("run-dispatched", "version-dispatched"))
    service.execute_dispatched(_dispatch_request("run-dispatched-cancel", "version-dispatched"))
    assert calls == ["run-dispatched"]
    assert (
        service.pipeline_run_service.get_run("run-dispatched-cancel", ctx=demo_admin_context())["status"] == "cancelled"
    )

    _insert_run(foundry, "run-lease-loss", "version-dispatched", status="queued")
    monkeypatch.setattr(
        service.pipeline_run_service,
        "execute_queued_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PipelineExecutionLeaseLost("lost")),
    )
    with pytest.raises(PipelineExecutionLeaseLost, match="lost"):
        service.execute_dispatched(_dispatch_request("run-lease-loss", "version-dispatched"))


def test_control_worker_observes_schedule_terminals_once_and_waits_for_active_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry(tmp_path)
    _insert_version(foundry, "version-control", _plan())
    with foundry.engine.begin() as transaction:
        transaction.execute(insert(db.pipeline_schedules).values(**_schedule_values()))
        transaction.execute(
            insert(db.pipeline_runs).values(
                **_run_values(
                    "run-terminal",
                    "version-control",
                    status="failed",
                    schedule_id="schedule-a",
                    completed_at=NOW,
                    error={"kind": "validation"},
                )
            )
        )
        transaction.execute(
            insert(db.pipeline_runs).values(
                **_run_values(
                    "run-missing-schedule",
                    "version-control",
                    status="succeeded",
                    schedule_id="missing-schedule",
                    completed_at=NOW,
                )
            )
        )
        transaction.execute(
            insert(db.pipeline_runs).values(**_run_values("run-active-cancel", "version-control", status="cancelling"))
        )
        transaction.execute(
            insert(db.pipeline_node_runs).values(
                id="node-active",
                tenant_id="tenant-demo",
                run_id="run-active-cancel",
                node_id="node-a",
                descriptor_id="dataset.input",
                spec_version="1",
                status="RUNNING",
                input_artifacts=[],
                output_artifacts=[],
                created_at=NOW,
                updated_at=NOW,
            )
        )

    control = foundry._services.pipelines.control
    assert control.recover_cancellations(tenant_id="tenant-demo", limit=0) == 0
    assert control.observe_schedule_terminals(tenant_id="tenant-demo", limit=1000) == 2
    assert control.observe_schedule_terminals(tenant_id="tenant-demo", limit=1000) == 0
    schedule = foundry.pipelines.get_schedule("pipeline-a", ctx=demo_admin_context())
    assert schedule is not None
    assert schedule["status"] == "paused"
    assert schedule["failureCount"] == 1
    assert schedule["lastTerminalRunId"] == "run-terminal"

    terminal_row = _run_row(foundry, "run-terminal")
    monkeypatch.setattr(
        control.pipeline_repository,
        "claim_terminal_schedule_observation",
        lambda **_kwargs: None,
    )
    assert control._observe_schedule_terminal(terminal_row) == 0
    terminal_row["schedule_id"] = None
    with foundry.engine.begin() as transaction:
        assert control._locked_schedule(transaction, terminal_row) is None
        control._record_observation(transaction, demo_admin_context(), None, terminal_row)


def test_distributed_worker_begin_execute_finalize_and_terminal_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = _foundry(tmp_path)
    plan = _plan()
    _insert_version(foundry, "version-worker", plan)
    _insert_run(foundry, "run-worker", "version-worker", status="queued")
    service = foundry._services.pipelines.distributed_node
    artifact = _runtime_artifact("node-a")

    def execute_stub(*_args: object, **_kwargs: object) -> PipelineV2RunResult:
        return PipelineV2RunResult(
            outputs=(),
            timeline=({"event": "pipeline.node.succeeded", "nodeId": "node-a"},),
            runtime_artifacts=(artifact,),
        )

    monkeypatch.setattr(PipelineDistributedNodeService, "_execute_runtime_node", execute_stub)
    begun = service.drive({**_payload("run-worker"), "operation": "begin"})
    outcome = service.drive(
        {
            **_payload("run-worker"),
            "operation": "execute_node",
            "node": plan["nodes"][0],
            "upstreamOutcomes": [{"nodeId": "source", "runtimeArtifact": pipeline_runtime_artifact_payload(artifact)}],
        }
    )
    finalized = service.drive(
        {
            **_payload("run-worker"),
            "operation": "finalize",
            "nodeOutcomes": [outcome],
        }
    )
    replay = service.drive({**_payload("run-worker"), "operation": "begin"})

    assert begun["status"] == "running"
    assert outcome["status"] == "succeeded"
    assert outcome["runtimeArtifact"]["nodeId"] == "node-a"
    assert finalized["status"] == "succeeded"
    assert replay["status"] == "succeeded"
    assert (
        service.drive({**_payload("run-worker"), "operation": "finalize", "nodeOutcomes": []})["status"] == "succeeded"
    )
    with pytest.raises(InvariantViolation, match="operation is invalid"):
        service.drive({**_payload("run-worker"), "operation": "unknown"})


def test_distributed_worker_cancellation_and_retry_evidence(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    plan = _plan(requires_stable_idempotency=False)
    _insert_version(foundry, "version-cancel", plan)
    _insert_run(foundry, "run-cancel", "version-cancel", status="cancelling", outputs=[{"ref": "kept"}])
    service = foundry._services.pipelines.distributed_node
    node_run, attempt = _insert_claimed_attempt(foundry, "run-cancel")

    cancelled = service.drive(
        {
            **_payload("run-cancel"),
            "operation": "cancel_node",
            "node": plan["nodes"][0],
        }
    )
    finalized = service.drive(
        {
            **_payload("run-cancel"),
            "operation": "finalize",
            "nodeOutcomes": [],
        }
    )
    assert cancelled["status"] == "cancelled"
    assert finalized == {"runId": "run-cancel", "status": "cancelled", "outputs": [{"ref": "kept"}]}

    _insert_run(foundry, "run-retry", "version-cancel", status="running", lease_token="run-lease")
    retry_node, retry_attempt = _insert_claimed_attempt(foundry, "run-retry")
    repository = service.pipeline_execution_repository
    with foundry.engine.begin() as transaction:
        repository.update_fenced_node_attempt_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            attempt_id=str(retry_attempt["id"]),
            worker_id="worker-a",
            lease_token="lease-a",
            fencing_token=int(retry_attempt["fencing_token"]),
            status="FAILED",
            output_manifest={},
            error={"kind": "adapter_transient"},
            error_kind="adapter_transient",
            completed_at=NOW,
        )
    row = _run_row(foundry, "run-retry")
    with foundry.engine.begin() as transaction:
        version = foundry.pipeline_repository.version_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            version_id="version-cancel",
        )
    assert version is not None
    with pytest.raises(PipelineNodeRetryableFailure, match="retry scheduled"):
        service._raise_if_retryable(
            demo_admin_context(),
            row,
            {},
            version,
            {"nodeId": "node-a", "status": "failed", "error": {"kind": "adapter_transient"}},
        )
    assert node_run["id"] == "run-cancel:node:node-a"
    assert attempt["attempt_number"] == 1
    assert retry_node["id"] == "run-retry:node:node-a"

    _insert_run(foundry, "run-not-cancelling", "version-cancel", status="running", lease_token="lease-run")
    assert service.cancel_node(
        {
            **_payload("run-not-cancelling"),
            "node": plan["nodes"][0],
        }
    ) == {"nodeId": "node-a", "status": "running"}
    _insert_run(foundry, "run-execute-cancelling", "version-cancel", status="cancelling")
    assert service.execute_node(
        {
            **_payload("run-execute-cancelling"),
            "node": plan["nodes"][0],
        }
    ) == {"nodeId": "node-a", "status": "cancelled"}


def test_distributed_worker_requires_promoted_deployment_evidence(tmp_path: Path) -> None:
    foundry = _foundry(tmp_path)
    plan = _plan()
    _insert_version(foundry, "version-deployment", plan)
    service = foundry._services.pipelines.distributed_node
    ctx = demo_admin_context()
    with foundry.engine.begin() as transaction:
        version = foundry.pipeline_repository.version_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id="version-deployment",
        )
    assert version is not None
    with pytest.raises(InvariantViolation, match="deployment evidence is missing"):
        service._deployment_id(ctx, version)
    with foundry.engine.begin() as transaction:
        transaction.execute(
            insert(db.pipeline_deployments).values(
                id="deployment-a",
                tenant_id=ctx.tenant_id,
                pipeline_id="pipeline-a",
                version_id="version-deployment",
                deployment_number=1,
                status="PROMOTED",
                execution_plan=plan,
                plan_fingerprint="plan-version-deployment",
                compiler_version="test",
                processor_pins=[],
                model_pins=[],
                function_pins=[],
                compute_profile={},
                idempotency_key="deployment-a",
                request_fingerprint="deployment-a",
                promoted_by=ctx.actor_user_id,
                promoted_at=NOW,
                created_by=ctx.actor_user_id,
                created_at=NOW,
            )
        )
    assert service._deployment_id(ctx, version) == "deployment-a"


def test_control_worker_expires_crashed_execution_but_spares_a_warm_lease(tmp_path: Path) -> None:
    """Reap runs whose executor died, and never reap one whose lease is still warm.

    A crashed async/distributed executor leaves a lapsed run-execution lease that
    nothing renews; only the synchronous replay path expired those, so those runs sat
    'running' forever. A healthy executor keeps its lease warm for the whole node
    (``run_execution_lease_keepalive``), so a future expiry must be left alone.
    """
    foundry = _foundry(tmp_path)
    _insert_version(foundry, "version-stale", _plan())
    with foundry.engine.begin() as transaction:
        transaction.execute(insert(db.pipeline_runs).values(**_crashed_run_values("run-crashed")))
        transaction.execute(
            insert(db.pipeline_runs).values(
                **_run_values(
                    "run-warm",
                    "version-stale",
                    status="executing",
                    execution_lease_token="warm-lease",
                    execution_lease_expires_at="2099-01-01T00:00:00Z",
                    execution_heartbeat_at=NOW,
                )
            )
        )

    control = foundry._services.pipelines.control
    totals = control.tick(limit=999)

    assert totals["staleExecutions"] == 1
    assert _run_row(foundry, "run-crashed")["status"] == "failed"
    assert _run_row(foundry, "run-warm")["status"] == "executing"
    # Idempotent: the already-terminal crashed run is not reaped a second time.
    assert control.recover_stale_executions(tenant_id="tenant-demo", limit=999) == 0


def test_distributed_node_keeps_the_run_lease_warm_so_a_long_node_is_not_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A node that outlives the run-lease window must not be reaped as a crash.

    The run lease is two minutes but a node may legitimately compute up to its policy
    timeout, and the distributed path otherwise renews the run lease only at
    evidence-write boundaries. Without the keepalive the stale-lease reaper expires a
    perfectly healthy long-running node, so a lapsed lease would stop meaning "dead
    executor". Assert the keepalive thread renews during the node and that the reaper
    then leaves the run alone.
    """
    foundry = _foundry(tmp_path)
    plan = _plan()
    _insert_version(foundry, "version-stale", plan)
    with foundry.engine.begin() as transaction:
        transaction.execute(insert(db.pipeline_runs).values(**_crashed_run_values("run-long")))

    # Wait on a signal from the renewal itself rather than on a clock, so the test is
    # deterministic and never races the background keepalive thread.
    renewed = Event()
    renewing_threads: list[str] = []
    original_renew = foundry.pipeline_repository.renew_run_execution_lease

    def signalling_renew(**kwargs: object) -> object:
        renewing_threads.append(current_thread().name)
        result = original_renew(**kwargs)  # type: ignore[arg-type]
        renewed.set()
        return result

    monkeypatch.setattr(foundry.pipeline_repository, "renew_run_execution_lease", signalling_renew)
    monkeypatch.setattr(pipeline_run_recovery, "_HEARTBEAT_INTERVAL_SECONDS", 0.0)

    def long_running_node(*_args: object, **_kwargs: object) -> PipelineV2RunResult:
        assert renewed.wait(10.0), "the keepalive never renewed the run lease"
        return PipelineV2RunResult(
            outputs=(),
            timeline=({"event": "pipeline.node.succeeded", "nodeId": "node-a"},),
            runtime_artifacts=(_runtime_artifact("node-a"),),
        )

    monkeypatch.setattr(PipelineDistributedNodeService, "_execute_runtime_node", long_running_node)
    outcome = foundry._services.pipelines.distributed_node.drive(
        {
            **_payload("run-long"),
            "operation": "execute_node",
            "node": plan["nodes"][0],
            "upstreamOutcomes": [],
        }
    )

    assert outcome["status"] == "succeeded"
    # The renewal came from the keepalive thread, not from an evidence-write boundary.
    assert any(name.startswith("pipeline-lease-run-long") for name in renewing_threads)
    expires_at = _run_row(foundry, "run-long")["execution_lease_expires_at"]
    assert expires_at is not None and str(expires_at) > "2020-01-01T00:00:00Z"
    assert foundry._services.pipelines.control.recover_stale_executions(tenant_id="tenant-demo", limit=999) == 0
    assert _run_row(foundry, "run-long")["status"] == "running"


def test_run_execution_lease_keepalive_renews_the_lease_during_long_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keepalive renews a claimed lease on its own cadence until the work ends."""
    foundry = _foundry(tmp_path)
    _insert_version(foundry, "version-stale", _plan())
    with foundry.engine.begin() as transaction:
        transaction.execute(insert(db.pipeline_runs).values(**_crashed_run_values("run-keepalive")))
    row = _run_row(foundry, "run-keepalive")
    guard = pipeline_run_recovery.PipelineExecutionLeaseGuard(
        foundry.engine,
        foundry.pipeline_repository,
        demo_admin_context(),
        row,
    )

    renewed = Event()
    original_renew = foundry.pipeline_repository.renew_run_execution_lease

    def signalling_renew(**kwargs: object) -> object:
        result = original_renew(**kwargs)  # type: ignore[arg-type]
        renewed.set()
        return result

    monkeypatch.setattr(foundry.pipeline_repository, "renew_run_execution_lease", signalling_renew)
    monkeypatch.setattr(pipeline_run_recovery, "_HEARTBEAT_INTERVAL_SECONDS", 0.0)

    with pipeline_run_recovery.run_execution_lease_keepalive(guard) as kept:
        assert renewed.wait(10.0), "the keepalive never renewed the run lease"

    assert kept is guard
    expires_at = _run_row(foundry, "run-keepalive")["execution_lease_expires_at"]
    assert expires_at is not None and str(expires_at) > "2020-01-01T00:00:00Z"


def _crashed_run_values(run_id: str) -> dict[str, object]:
    """A run holding an execution lease that lapsed long ago: a crashed executor."""
    return _run_values(
        run_id,
        "version-stale",
        status="running",
        execution_lease_token=f"{run_id}-lease",
        execution_lease_expires_at="2020-01-01T00:00:00Z",
        execution_heartbeat_at="2020-01-01T00:00:00Z",
    )


def _foundry(tmp_path: Path) -> FoundryLite:
    return FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))


def _enqueue(
    service: PipelineAsyncRunService,
    ctx: RequestContext,
    key: str,
    *,
    wait_seconds: int = 0,
) -> dict[str, object]:
    return service.enqueue(
        "pipeline-a",
        version_id="version-async",
        idempotency_key=key,
        parameters={"key": key},
        target_node_ids=None,
        wait_seconds=wait_seconds,
        ctx=ctx,
    )


def _insert_version(foundry: FoundryLite, version_id: str, plan: dict[str, object]) -> None:
    with foundry.engine.begin() as transaction:
        transaction.execute(
            insert(db.pipeline_versions).values(
                id=version_id,
                tenant_id="tenant-demo",
                pipeline_id="pipeline-a",
                version_number=sum(ord(character) for character in version_id),
                graph={"nodes": [], "edges": []},
                graph_fingerprint=f"graph-{version_id}",
                execution_plan=plan,
                plan_fingerprint=f"plan-{version_id}",
                compiler_version="test",
                proposal_id=f"proposal-{version_id}",
                created_by="user-demo-admin",
                created_at=NOW,
                deployed_at=NOW,
            )
        )


def _insert_run(
    foundry: FoundryLite,
    run_id: str,
    version_id: str,
    *,
    status: str,
    outputs: list[dict[str, object]] | None = None,
    lease_token: str | None = None,
) -> None:
    with foundry.engine.begin() as transaction:
        transaction.execute(
            insert(db.pipeline_runs).values(
                **_run_values(
                    run_id,
                    version_id,
                    status=status,
                    outputs=outputs,
                    execution_lease_token=lease_token,
                    execution_lease_expires_at="2099-01-01T00:00:00Z" if lease_token else None,
                    execution_heartbeat_at=NOW if lease_token else None,
                )
            )
        )


def _run_values(
    run_id: str,
    version_id: str,
    *,
    status: str,
    outputs: list[dict[str, object]] | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": run_id,
        "tenant_id": "tenant-demo",
        "pipeline_id": "pipeline-a",
        "version_id": version_id,
        "status": status,
        "idempotency_key": run_id,
        "request_fingerprint": run_id,
        "plan_fingerprint": f"plan-{version_id}",
        "parameters": {},
        "target_node_ids": [],
        "outputs": outputs or [],
        "timeline": [{"event": f"pipeline.run.{status}", "at": NOW}],
        "created_by": "user-demo-admin",
        "started_at": NOW,
        **extra,
    }


def _schedule_values() -> dict[str, object]:
    return {
        "id": "schedule-a",
        "tenant_id": "tenant-demo",
        "pipeline_id": "pipeline-a",
        "version_id": "version-control",
        "schedule": {
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": 300,
            "autoPauseAfterFailures": 1,
        },
        "enabled": True,
        "status": "active",
        "updated_by": "user-demo-admin",
        "updated_at": NOW,
        "trigger_type": "interval",
        "timezone": "UTC",
        "next_due_at": NOW,
    }


def _plan(*, requires_stable_idempotency: bool = False) -> dict[str, object]:
    return {
        "nodes": [
            {
                "nodeId": "node-a",
                "kind": "dataset.input",
                "descriptorId": "dataset.input",
                "specVersion": 1,
                "runtimeCapability": "dataset_runtime",
                "config": {},
                "executionPolicy": {
                    "maximumAttempts": 3,
                    "requiresStableIdempotency": requires_stable_idempotency,
                },
            }
        ],
        "edges": [],
        "sourceContracts": [],
    }


def _payload(run_id: str) -> dict[str, object]:
    return {
        "tenant_id": "tenant-demo",
        "request_id": f"request-{run_id}",
        "run_id": run_id,
        "workerIdentity": "worker-a",
        "externalExecutionId": f"activity-{run_id}",
    }


def _runtime_artifact(node_id: str) -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id=node_id,
        descriptor_id="dataset.input",
        spec_version=1,
        port_id="output",
        artifact_kind="dataset_version",
        plane="data",
        items=({"value": 1},),
        artifact_ref={"datasetRef": "dataset-a", "versionId": "version-a"},
        manifest={},
        security_envelope={"classification": "INTERNAL"},
        status="COMMITTED",
        is_serving=False,
        committed_at=NOW,
    )


def _dispatch_request(run_id: str, version_id: str) -> PipelineDagDispatchRequest:
    return PipelineDagDispatchRequest(
        tenant_id="tenant-demo",
        run_id=run_id,
        pipeline_id="pipeline-a",
        version_id=version_id,
        request_id=f"request-{run_id}",
        idempotency_key=run_id,
        execution_plan=_plan(),
        target_node_ids=(),
    )


def _insert_claimed_attempt(foundry: FoundryLite, run_id: str) -> tuple[dict[str, object], dict[str, object]]:
    repository = foundry._services.pipelines.distributed_node.pipeline_execution_repository
    with foundry.engine.begin() as transaction:
        repository.insert_node_run(
            transaction=transaction,
            record=PipelineNodeRunRecord(
                node_run_id=f"{run_id}:node:node-a",
                tenant_id="tenant-demo",
                run_id=run_id,
                node_id="node-a",
                descriptor_id="dataset.input",
                spec_version="1",
                input_artifacts=[],
                created_at=NOW,
            ),
        )
        node = repository.node_run_by_run_node(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_id=run_id,
            node_id="node-a",
        )
        assert node is not None
        attempt = repository.claim_node_attempt(
            transaction=transaction,
            claim=PipelineNodeAttemptClaim(
                tenant_id="tenant-demo",
                node_run_id=str(node["id"]),
                worker_id="worker-a",
                lease_token="lease-a",
                lease_expires_at="2099-01-01T00:00:00Z",
                heartbeat_at=NOW,
                executor_profile="test",
                input_manifest={},
                external_execution_id=f"activity-{run_id}",
            ),
        )
        assert attempt is not None
    return dict(node), dict(attempt)


def _run_row(foundry: FoundryLite, run_id: str) -> PipelineRunRow:
    with foundry.engine.begin() as transaction:
        row = foundry.pipeline_repository.run_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_id=run_id,
        )
    assert row is not None
    return row
