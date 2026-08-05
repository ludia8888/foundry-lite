"""Contract proof for durable Action notification policy persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from foundry_lite.application.ports.action_notification_policy_repository import (
    ActionNotificationPolicyIdempotencyRecord,
    ActionNotificationPolicyRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.action_notification_policy_repository import (
    SqlAlchemyActionNotificationPolicyRepository,
)
from sqlalchemy import create_engine


def test_notification_policy_repository_contract_cas_directory_and_idempotency() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionNotificationPolicyRepository(engine)
    with engine.begin() as transaction:
        created = repository.insert_policy_if_absent(transaction=transaction, record=_policy())
        duplicate = repository.insert_policy_if_absent(transaction=transaction, record=_policy("policy-2"))
        assert created is not None and duplicate is None
        assert (
            repository.policy_by_target(
                transaction=transaction,
                tenant_id="tenant-a",
                target_ref="notification-policy:operations",
            )
            == created
        )
        stale = repository.update_policy(
            transaction=transaction,
            tenant_id="tenant-a",
            policy_name="operations",
            expected_fingerprint="sha256:stale",
            display_name="Operations v2",
            delivery_mode="best_effort",
            recipients=({"userId": "user-2", "roles": ["ops_manager"]},),
            status="active",
            config_fingerprint="sha256:v2",
            actor_user_id="admin-2",
            updated_at="2026-08-05T00:01:00Z",
        )
        assert stale is None
        inserted = repository.insert_idempotency_or_existing(transaction=transaction, record=_idempotency())
        existing = repository.insert_idempotency_or_existing(transaction=transaction, record=_idempotency("idem-2"))
        assert inserted is None
        assert existing is not None and existing["id"] == "idem-1"
    policy = repository.policy_for(tenant_id="tenant-a", target_ref="notification-policy:operations")
    assert policy is not None and policy.recipients[0].user_id == "user-1"


def test_postgres_notification_policy_concurrent_create_has_one_winner(postgres_fixture) -> None:
    repository = SqlAlchemyActionNotificationPolicyRepository(postgres_fixture.engine)
    barrier = Barrier(2)

    def create(index: int):
        with postgres_fixture.engine.begin() as transaction:
            barrier.wait()
            return repository.insert_policy_if_absent(
                transaction=transaction,
                record=_policy(f"policy-{index}", tenant_id="tenant-demo"),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(create, (1, 2)))
    assert sum(row is not None for row in rows) == 1


def _policy(policy_id: str = "policy-1", *, tenant_id: str = "tenant-a") -> ActionNotificationPolicyRecord:
    return ActionNotificationPolicyRecord(
        policy_id=policy_id,
        tenant_id=tenant_id,
        policy_name="operations",
        target_ref="notification-policy:operations",
        display_name="Operations",
        delivery_mode="strict",
        recipients=({"userId": "user-1", "roles": ["admin"]},),
        status="active",
        version=1,
        config_fingerprint="sha256:v1",
        actor_user_id="admin-1",
        created_at="2026-08-05T00:00:00Z",
    )


def _idempotency(record_id: str = "idem-1") -> ActionNotificationPolicyIdempotencyRecord:
    return ActionNotificationPolicyIdempotencyRecord(
        record_id=record_id,
        tenant_id="tenant-a",
        actor_user_id="admin-1",
        operation="create:operations",
        idempotency_key="request-1",
        request_fingerprint="sha256:request",
        response_json={"policyName": "operations"},
        created_at="2026-08-05T00:00:00Z",
    )
