from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionEffectClaim,
    ActionEffectOperationRecord,
    ActionEffectReceiptRecord,
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


def test_action_effect_receipt_contract_fences_takeover_and_terminal_write() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_run(transaction=transaction, record=_run(), steps=(_step(),))
        receipt = repository.insert_effect_receipt(transaction=transaction, record=_effect_receipt())
        duplicate = repository.insert_effect_receipt(transaction=transaction, record=_effect_receipt())
        assert receipt is not None
        assert duplicate is None
        first = repository.claim_effect_receipt(
            transaction=transaction,
            claim=ActionEffectClaim("tenant-a", receipt["id"], "worker-1", "lease-1", "01", "00"),
        )
        assert first is not None and first["fencing_token"] == 1
        blocked = repository.claim_effect_receipt(
            transaction=transaction,
            claim=ActionEffectClaim("tenant-a", receipt["id"], "worker-2", "lease-2", "02", "00"),
        )
        assert blocked is None
    with engine.begin() as transaction:
        takeover = repository.claim_effect_receipt(
            transaction=transaction,
            claim=ActionEffectClaim("tenant-a", receipt["id"], "worker-2", "lease-2", "03", "02"),
        )
        assert takeover is not None and takeover["fencing_token"] == 2
        stale = repository.complete_effect_receipt(
            transaction=transaction,
            tenant_id="tenant-a",
            receipt_id=receipt["id"],
            worker_id="worker-1",
            lease_token="lease-1",
            fencing_token=1,
            status="succeeded",
            response={},
            error=None,
            retry_at=None,
            external_execution_id="remote-stale",
            completed_at="02",
        )
        assert stale is None
        completed = repository.complete_effect_receipt(
            transaction=transaction,
            tenant_id="tenant-a",
            receipt_id=receipt["id"],
            worker_id="worker-2",
            lease_token="lease-2",
            fencing_token=2,
            status="succeeded",
            response={"providerStatus": 202},
            error=None,
            retry_at=None,
            external_execution_id="remote-1",
            completed_at="02",
        )
        assert completed is not None
        assert completed["status"] == "succeeded"
        assert completed["external_execution_id"] == "remote-1"


def test_action_effect_operator_contract_cancels_before_dispatch_and_fences_worker() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_run(transaction=transaction, record=_run(), steps=(_step(),))
        receipt = repository.insert_effect_receipt(transaction=transaction, record=_effect_receipt())
        assert receipt is not None
        claimed = repository.claim_effect_receipt(
            transaction=transaction,
            claim=ActionEffectClaim("tenant-a", receipt["id"], "worker-1", "lease-1", "03", "01"),
        )
        assert claimed is not None
        cancelled = repository.request_effect_cancel(
            transaction=transaction,
            tenant_id="tenant-a",
            receipt_id=receipt["id"],
            reason="operator",
            requested_at="02",
        )
        assert cancelled is not None and cancelled["status"] == "cancelled"
        assert cancelled["fencing_token"] == 2
        assert (
            repository.start_effect_dispatch(
                transaction=transaction,
                tenant_id="tenant-a",
                receipt_id=receipt["id"],
                worker_id="worker-1",
                lease_token="lease-1",
                fencing_token=1,
                started_at="02",
            )
            is None
        )
        assert (
            repository.complete_effect_receipt(
                transaction=transaction,
                tenant_id="tenant-a",
                receipt_id=receipt["id"],
                worker_id="worker-1",
                lease_token="lease-1",
                fencing_token=1,
                status="succeeded",
                response={},
                error=None,
                retry_at=None,
                external_execution_id="stale",
                completed_at="02",
            )
            is None
        )


def test_action_effect_operator_contract_retries_and_reconciles_with_durable_replay() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionExecutionRepository(engine)
    with engine.begin() as transaction:
        repository.insert_run(transaction=transaction, record=_run(), steps=(_step(),))
        dead = repository.insert_effect_receipt(transaction=transaction, record=_effect_receipt())
        unknown = repository.insert_effect_receipt(
            transaction=transaction,
            record=_effect_receipt(receipt_id="effect-2", effect_id="notify-finance"),
        )
        assert dead is not None and unknown is not None
        _complete_effect(repository, transaction, dead, "dead_letter")
        _complete_effect(repository, transaction, unknown, "outcome_unknown")
        retried = repository.retry_effect_receipt(
            transaction=transaction,
            tenant_id="tenant-a",
            receipt_id=dead["id"],
            requested_at="03",
        )
        assert retried is not None and retried["status"] == "retry_wait"
        reconciled = repository.reconcile_effect_receipt(
            transaction=transaction,
            tenant_id="tenant-a",
            receipt_id=unknown["id"],
            resolution="confirmed_delivered",
            evidence={"externalExecutionId": "provider-2", "providerReference": "case-2"},
            actor_user_id="ops-1",
            reconciled_at="03",
        )
        assert reconciled is not None and reconciled["status"] == "succeeded"
        assert reconciled["reconciled_by_user_id"] == "ops-1"
        record = ActionEffectOperationRecord(
            "operation-1",
            "tenant-a",
            "ops-1",
            unknown["id"],
            "reconcile",
            "idem-reconcile",
            "sha256:request",
            {"receiptId": unknown["id"], "status": "succeeded"},
            "03",
        )
        assert repository.insert_effect_operation_or_existing(transaction=transaction, record=record) is None
        replay = repository.insert_effect_operation_or_existing(transaction=transaction, record=record)
        assert replay is not None and replay["response_json"]["status"] == "succeeded"


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


def _effect_receipt(*, receipt_id: str = "effect-1", effect_id: str = "notify-ops") -> ActionEffectReceiptRecord:
    return ActionEffectReceiptRecord(
        receipt_id=receipt_id,
        tenant_id="tenant-a",
        action_run_id="run-1",
        effect_id=effect_id,
        phase="after_commit",
        effect_kind="notification",
        target_ref="policy:order-ops",
        idempotency_key=f"action-effect:run-1:{effect_id}",
        max_attempts=3,
        request={"effect": {}},
        created_at="00",
    )


def _complete_effect(
    repository: SqlAlchemyActionExecutionRepository,
    transaction,
    receipt,
    status: str,
) -> None:
    claimed = repository.claim_effect_receipt(
        transaction=transaction,
        claim=ActionEffectClaim("tenant-a", receipt["id"], "worker-1", f"lease-{receipt['id']}", "04", "01"),
    )
    assert claimed is not None
    completed = repository.complete_effect_receipt(
        transaction=transaction,
        tenant_id="tenant-a",
        receipt_id=receipt["id"],
        worker_id="worker-1",
        lease_token=f"lease-{receipt['id']}",
        fencing_token=claimed["fencing_token"],
        status=status,
        response=None,
        error={"kind": status},
        retry_at=None,
        external_execution_id=None,
        completed_at="02",
    )
    assert completed is not None
