from __future__ import annotations

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext

from tests.conftest import prepare_indexed_demo


def test_start_restore_mode_closes_traffic_without_running_storage_preflight(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    mode_service = foundry.operations._backup_restore.backup_restore_mode_service
    monkeypatch.setattr(
        mode_service.backup_restore_preflight_service,
        "restore_preflight_report",
        _unexpected_preflight,
    )

    mode = foundry.operations.start_restore_mode(
        ctx=ctx,
        backup_id="backup-fast-gate",
        restore_id="restore-fast-gate",
    )

    assert mode["status"] == "paused"
    assert mode["preflightStatus"] == "pending"
    assert mode["is_write_traffic_paused"] is True
    assert mode["is_outbox_publisher_paused"] is True
    assert mode["is_serving_traffic_open"] is False


def test_exact_validation_replay_skips_storage_preflight(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _prepare_closed_loop(foundry)
    foundry.operations.start_restore_mode(
        ctx=ctx,
        backup_id="backup-single-snapshot",
        restore_id="restore-single-snapshot",
    )
    validation = foundry.operations.run_post_restore_validation(
        "restore-single-snapshot",
        ctx=ctx,
        validation_id="validation-single-snapshot",
    )
    mode_service = foundry.operations._backup_restore.backup_restore_mode_service
    monkeypatch.setattr(
        mode_service.backup_restore_preflight_service,
        "restore_preflight_report",
        _unexpected_preflight,
    )

    replay = foundry.operations.run_post_restore_validation(
        "restore-single-snapshot",
        ctx=ctx,
        validation_id="validation-single-snapshot",
    )

    assert replay == validation


def test_resume_approval_reuses_passed_validation_without_storage_preflight(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _prepare_closed_loop(foundry)
    foundry.operations.start_restore_mode(
        ctx=ctx,
        backup_id="backup-approved-evidence",
        restore_id="restore-approved-evidence",
    )
    foundry.operations.run_post_restore_validation(
        "restore-approved-evidence",
        ctx=ctx,
        validation_id="validation-approved-evidence",
    )
    mode_service = foundry.operations._backup_restore.backup_restore_mode_service
    monkeypatch.setattr(
        mode_service.backup_restore_preflight_service,
        "restore_preflight_report",
        _unexpected_preflight,
    )

    approved = foundry.operations.approve_restore_resume(
        "restore-approved-evidence",
        ctx=ctx,
        validation_id="validation-approved-evidence",
    )

    assert approved["status"] == "resume_approved"
    assert approved["preflightStatus"] == "ready"
    assert approved["is_serving_traffic_open"] is True
    assert approved["is_outbox_publisher_paused"] is False


def _prepare_closed_loop(foundry: FoundryLite) -> RequestContext:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "backup restore validation"},
        idempotency_key=f"backup-restore-{ctx.request_id}",
        ctx=ctx,
    )
    foundry.materialization.run("action_log", ctx=ctx)
    return ctx


def _unexpected_preflight(**_kwargs: object) -> object:
    raise AssertionError("this path must reuse durable validation instead of rebuilding preflight evidence")
