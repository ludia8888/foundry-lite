from __future__ import annotations

from typing import Any, cast

from foundry_lite.application.services.backup_restore_mode import (
    POST_RESTORE_VALIDATED_EVENT,
    RESTORE_MODE_STARTED_EVENT,
)
from foundry_lite.application.services.backup_restore_mode_service import BackupRestoreModeService
from foundry_lite.application.services.backup_restore_validation import BackupRestoreValidationService
from foundry_lite.domain.context import demo_admin_context


class _RuntimeSnapshotRepository:
    def __init__(self, audit_events: list[dict[str, object]]) -> None:
        self.audit_events = audit_events
        self.calls = 0

    def list_runs(self, *, tenant_id: str) -> dict[str, object]:
        assert tenant_id == "tenant-demo"
        self.calls += 1
        return {"auditEvents": self.audit_events}


class _RuntimePermissionBoundary:
    def _require_or_audit(self, *_args: object, **_kwargs: object) -> None:
        return None


class _UnexpectedPreflight:
    def restore_preflight_report(self, **_kwargs: object) -> object:
        raise AssertionError("exact validation replay must not rebuild preflight evidence")


def test_exact_validation_replay_uses_one_snapshot_and_skips_preflight() -> None:
    restore_id = "restore-single-snapshot"
    validation_id = "validation-single-snapshot"
    current = {"restoreId": restore_id, "status": "paused"}
    validation = {"restoreId": restore_id, "validationId": validation_id, "status": "passed"}
    repository = _RuntimeSnapshotRepository(
        [
            {
                "event_type": RESTORE_MODE_STARTED_EVENT,
                "resource_id": restore_id,
                "created_at": "2026-08-23T00:00:00+00:00",
                "after_ref": current,
            },
            {
                "event_type": POST_RESTORE_VALIDATED_EVENT,
                "resource_id": restore_id,
                "created_at": "2026-08-23T00:01:00+00:00",
                "after_ref": validation,
            },
        ]
    )
    validation_service = object.__new__(BackupRestoreValidationService)
    validation_service.runtime_repository = cast(Any, repository)
    mode_service = object.__new__(BackupRestoreModeService)
    mode_service.runtime_service = cast(Any, _RuntimePermissionBoundary())
    mode_service.backup_restore_validation_service = validation_service
    mode_service.backup_restore_preflight_service = cast(Any, _UnexpectedPreflight())

    replay = mode_service.run_post_restore_validation(
        restore_id,
        ctx=demo_admin_context(),
        validation_id=validation_id,
    )

    assert replay == validation
    assert repository.calls == 1
