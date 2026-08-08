"""Contract proof for durable Action notification policy persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from foundry_lite.application.ports.action_notification_policy_repository import (
    ActionNotificationPolicyIdempotencyRecord,
    ActionNotificationPolicyIntegrityError,
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


@pytest.mark.parametrize("is_same_name", (True, False))
def test_postgres_notification_policy_concurrent_create_has_one_winner(postgres_fixture, *, is_same_name: bool) -> None:
    repository = SqlAlchemyActionNotificationPolicyRepository(postgres_fixture.engine)
    barrier = Barrier(2)

    def create(index: int):
        with postgres_fixture.engine.begin() as transaction:
            barrier.wait()
            return repository.insert_policy_if_absent(
                transaction=transaction,
                record=replace(
                    _policy(f"policy-{index}", tenant_id="tenant-demo"),
                    policy_name="operations" if is_same_name else f"operations-{index}",
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(create, (1, 2)))
    assert sum(row is not None for row in rows) == 1


def test_notification_policy_insert_ignores_either_business_key_conflict() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionNotificationPolicyRepository(engine)
    with engine.begin() as transaction:
        assert repository.insert_policy_if_absent(transaction=transaction, record=_policy()) is not None
        same_name = replace(_policy("policy-2"), target_ref="notification-policy:other")
        same_target = replace(_policy("policy-3"), policy_name="other")
        assert repository.insert_policy_if_absent(transaction=transaction, record=same_name) is None
        assert repository.insert_policy_if_absent(transaction=transaction, record=same_target) is None


def test_notification_policy_insert_does_not_hide_policy_id_collision() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionNotificationPolicyRepository(engine)
    with engine.begin() as transaction:
        assert repository.insert_policy_if_absent(transaction=transaction, record=_policy()) is not None
        id_collision = replace(
            _policy(),
            policy_name="other",
            target_ref="notification-policy:other",
        )
        with pytest.raises(
            ActionNotificationPolicyIntegrityError,
            match="without a matching business-key conflict",
        ):
            repository.insert_policy_if_absent(transaction=transaction, record=id_collision)
        assert (
            repository.policy_by_name(
                transaction=transaction,
                tenant_id="tenant-a",
                policy_name="operations",
            )
            is not None
        )
        assert (
            repository.policy_by_name(
                transaction=transaction,
                tenant_id="tenant-a",
                policy_name="other",
            )
            is None
        )
        assert (
            repository.policy_by_target(
                transaction=transaction,
                tenant_id="tenant-a",
                target_ref="notification-policy:other",
            )
            is None
        )


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
