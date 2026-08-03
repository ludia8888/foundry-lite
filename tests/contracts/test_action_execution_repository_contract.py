from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionRunEventRecord,
    ActionRunStepRecord,
    ActionStepAttemptClaim,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.action_execution_repository import SqlAlchemyActionExecutionRepository
from sqlalchemy import create_engine


def test_action_execution_repository_contract_fences_takeover_and_sequences_events() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionExecutionRepository(engine)
    with engine.begin() as transaction:
        row = repository.insert_run(transaction=transaction, record=_run(), steps=(_step(),))
        assert row is not None
        first = repository.claim_step(transaction=transaction, claim=_claim("worker-1", "lease-1", "00", "01"))
        assert first is not None
        blocked = repository.claim_step(transaction=transaction, claim=_claim("worker-2", "lease-2", "00", "02"))
        assert blocked is None
    with engine.begin() as transaction:
        takeover = repository.claim_step(transaction=transaction, claim=_claim("worker-2", "lease-2", "02", "03"))
        assert takeover is not None
        assert takeover["fencing_token"] == 2
        stale = repository.lock_attempt_owner(
            transaction=transaction,
            tenant_id="tenant-a",
            attempt_id=str(first["id"]),
            worker_id="worker-1",
            lease_token="lease-1",
            fencing_token=1,
            owned_at="02",
        )
        assert stale is None
        one = repository.append_event(transaction=transaction, record=_event("e1"))
        two = repository.append_event(transaction=transaction, record=_event("e2"))
        assert (one["sequence"], two["sequence"]) == (1, 2)
        dispatched = repository.update_dispatch(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id="run-1",
            workflow_run_id="workflow-1",
            dispatch_status="dispatched",
            dispatch_error=None,
        )
        duplicate = repository.update_dispatch(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id="run-1",
            workflow_run_id="workflow-1",
            dispatch_status="dispatched",
            dispatch_error=None,
        )
        assert dispatched is not None
        assert duplicate is None

        cancelling = repository.request_cancel(
            transaction=transaction,
            tenant_id="tenant-a",
            run_id="run-1",
            idempotency_key="cancel-1",
            request_fingerprint="sha256:cancel",
            reason="operator",
            requested_at="02",
        )
        assert cancelling is not None and cancelling["status"] == "cancelling"
        assert [
            row["id"] for row in repository.cancelling_runs(transaction=transaction, tenant_id="tenant-a", limit=10)
        ] == ["run-1"]


def test_postgres_action_step_concurrent_claim_has_one_winner(postgres_fixture) -> None:
    repository = SqlAlchemyActionExecutionRepository(postgres_fixture.engine)
    with postgres_fixture.engine.begin() as transaction:
        repository.insert_run(
            transaction=transaction,
            record=_run(tenant_id="tenant-test"),
            steps=(_step(tenant_id="tenant-test"),),
        )
    barrier = Barrier(2)

    def claim(worker_id: str):
        with postgres_fixture.engine.begin() as transaction:
            barrier.wait()
            return repository.claim_step(
                transaction=transaction,
                claim=_claim(worker_id, f"lease-{worker_id}", "00", "01", tenant_id="tenant-test"),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-1", "worker-2")))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0]["fencing_token"] == 1


def _run(*, tenant_id: str = "tenant-a") -> ActionAsyncRunRecord:
    return ActionAsyncRunRecord(
        run_id="run-1",
        tenant_id=tenant_id,
        action_type_id="action-1",
        action_api_name="ApproveOrder",
        actor_user_id="user-1",
        target_object_type_id="otype-1",
        target_object_type="Order",
        target_object_id="O-1",
        expected_object_version=1,
        parameters={},
        idempotency_key="idem-1",
        request_fingerprint="sha256:req",
        definition_version="sha256:def",
        plan_hash="sha256:plan",
        execution_plan={},
        created_at="00",
    )


def _step(*, tenant_id: str = "tenant-a") -> ActionRunStepRecord:
    return ActionRunStepRecord("step-1", tenant_id, "run-1", "function", "function", {}, "00")


def _claim(
    worker: str, lease: str, claimed: str, expires: str, *, tenant_id: str = "tenant-a"
) -> ActionStepAttemptClaim:
    return ActionStepAttemptClaim(tenant_id, "run-1", "function", worker, lease, expires, claimed, {})


def _event(event_id: str) -> ActionRunEventRecord:
    return ActionRunEventRecord(event_id, "tenant-a", "run-1", "action.run.test", {}, "02")
