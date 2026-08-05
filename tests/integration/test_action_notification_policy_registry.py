"""End-to-end application proof for no-code notification policy governance."""

from __future__ import annotations

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select


def test_notification_policy_crud_is_audited_idempotent_and_fail_closed(tmp_path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    created = foundry.actions.create_notification_policy(
        "travelOps",
        display_name="Travel operations",
        delivery_mode="strict",
        recipients=[{"userId": "operator-1", "roles": ["ops_manager"]}],
        idempotency_key="create-travel-ops",
        ctx=ctx,
    )
    replay = foundry.actions.create_notification_policy(
        "travelOps",
        display_name="Travel operations",
        delivery_mode="strict",
        recipients=[{"userId": "operator-1", "roles": ["ops_manager"]}],
        idempotency_key="create-travel-ops",
        ctx=ctx,
    )
    assert replay == created
    updated = foundry.actions.update_notification_policy(
        "travelOps",
        display_name="Travel and dining operations",
        delivery_mode="best_effort",
        recipients=[
            {"userId": "operator-1", "roles": ["ops_manager"]},
            {"userId": "operator-2", "roles": ["admin"]},
        ],
        status="active",
        expected_fingerprint=str(created["configFingerprint"]),
        idempotency_key="update-travel-ops",
        ctx=ctx,
    )
    assert updated["version"] == 2
    assert foundry.actions.get_notification_policy("travelOps", ctx=ctx) == updated
    disabled = foundry.actions.disable_notification_policy(
        "travelOps",
        expected_fingerprint=str(updated["configFingerprint"]),
        idempotency_key="disable-travel-ops",
        ctx=ctx,
    )
    assert disabled["status"] == "disabled" and disabled["version"] == 3
    directory = dependencies.action_notification_recipient_directory
    assert directory.policy_for(tenant_id=ctx.tenant_id, target_ref="notification-policy:travelOps") is None
    assert _count(foundry, db.audit_events) == 3
    assert _count(foundry, db.outbox_events) == 3


def test_notification_policy_rejects_stale_cas_idempotency_drift_and_unauthorized_user(tmp_path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path))
    ctx = demo_admin_context()
    created = foundry.actions.create_notification_policy(
        "operations2",
        display_name="Operations",
        delivery_mode="strict",
        recipients=[{"userId": "operator-1", "roles": ["admin"]}],
        idempotency_key="create-operations-2",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected):
        foundry.actions.update_notification_policy(
            "operations2",
            display_name="Changed",
            delivery_mode="strict",
            recipients=[{"userId": "operator-1", "roles": ["admin"]}],
            status="active",
            expected_fingerprint="sha256:stale",
            idempotency_key="stale-update",
            ctx=ctx,
        )
    with pytest.raises(ConflictDetected):
        foundry.actions.create_notification_policy(
            "operations2",
            display_name="Different",
            delivery_mode="strict",
            recipients=[{"userId": "operator-1", "roles": ["admin"]}],
            idempotency_key="create-operations-2",
            ctx=ctx,
        )
    viewer = RequestContext(tenant_id=ctx.tenant_id, actor_user_id="viewer-1", roles=("viewer",))
    with pytest.raises(PermissionDenied):
        foundry.actions.get_notification_policy("operations2", ctx=viewer)
    assert created["version"] == 1


def _count(foundry: FoundryLite, table) -> int:
    with foundry.engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())
