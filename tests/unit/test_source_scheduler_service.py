from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from foundry_lite.application.services.source_scheduler_service import SourceSchedulerService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class _Policy:
    def __init__(self) -> None:
        self.permissions: list[str] = []

    def require(self, _ctx: RequestContext, permission: str) -> None:
        self.permissions.append(permission)


class _Engine:
    def __init__(self) -> None:
        self.conn = object()

    def begin(self) -> _Engine:
        return self

    def __enter__(self) -> object:
        return self.conn

    def __exit__(self, *_args: object) -> None:
        return None


class _SourceRepo:
    def __init__(self, runs: list[dict[str, object]]) -> None:
        self.runs = runs
        self.updated: list[dict[str, Any]] = []

    def list_syncs(self, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "sync_name": "ignored",
                "source_name": "ignored_source",
                "status": "paused",
                "schedule": {"mode": "interval", "everySeconds": 60},
            },
            {
                "sync_name": "orders_hourly",
                "source_name": "orders_source",
                "status": "active",
                "created_at": "2026-01-01T00:00:00Z",
                "schedule": {"mode": "interval", "everySeconds": 3600, "batchLimit": 5},
            },
        ]

    def list_sync_runs(self, **kwargs: object) -> list[dict[str, object]]:
        return self.runs if kwargs.get("sync_name") == "orders_hourly" else []

    def update_sync_run_result(self, **kwargs: object) -> dict[str, object]:
        self.updated.append(dict(kwargs))
        return {
            "id": kwargs["run_id"],
            "status": kwargs["status"],
            "idempotency_key": "old-slot",
        }


class _RuntimeRepo:
    def __init__(self, workflow: dict[str, object] | None) -> None:
        self.workflow = workflow

    def workflow_run_by_id(self, **_kwargs: object) -> dict[str, object] | None:
        return self.workflow


class _SourceRegistry:
    def source_by_name(self, **_kwargs: object) -> None:
        return None


class _Management:
    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []

    def start_managed_sync_run(self, sync_name: str, **kwargs: object) -> dict[str, object]:
        self.started.append({"sync_name": sync_name, **kwargs})
        return {"runId": "scheduled-run-1", "triggerType": kwargs["trigger_type"]}


class _RuntimeService:
    def __init__(self) -> None:
        self.audits: list[dict[str, object]] = []

    def _audit(self, _conn: object, _ctx: RequestContext, **kwargs: object) -> None:
        self.audits.append(dict(kwargs))


def _service(
    repo: _SourceRepo,
    runtime_repo: _RuntimeRepo,
    management: _Management,
    policy: _Policy,
) -> SourceSchedulerService:
    service = SourceSchedulerService(
        engine=_Engine(),
        policy=policy,
        source_management_repository=repo,
        source_registry_repository=_SourceRegistry(),
        runtime_repository=runtime_repo,
    )
    service.bind_collaborators(
        {
            "source_management_service": management,
            "runtime_service": _RuntimeService(),
        }
    )
    return service


def test_source_scheduler_service_reconciles_terminal_workflow_before_starting_due_run() -> None:
    repo = _SourceRepo([{"id": "run-old", "status": "running", "workflow_run_id": "workflow-1"}])
    runtime_repo = _RuntimeRepo({"status": "succeeded", "output": {"committedVersionId": "version-1"}})
    management = _Management()
    policy = _Policy()
    service = _service(repo, runtime_repo, management, policy)

    result = service.run_due_managed_syncs(
        ctx=RequestContext(tenant_id="tenant-a"),
        now=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        max_runs=1,
    )

    assert policy.permissions == ["source:write"]
    assert repo.updated[0]["status"] == "succeeded"
    assert repo.updated[0]["dataset_version_id"] == "version-1"
    assert repo.updated[0]["error"] is None
    assert isinstance(service.runtime_service, _RuntimeService)
    assert service.runtime_service.audits == [
        {
            "event_type": "source.sync_run.scheduler_reconciled",
            "resource_type": "source_sync_run",
            "resource_id": "run-old",
            "action": "reconcile_scheduled_run",
            "after_ref": {"status": "succeeded", "workflowRunId": "workflow-1"},
            "correlation_id": "run-old",
        }
    ]
    assert management.started[0]["sync_name"] == "orders_hourly"
    assert management.started[0]["trigger_type"] == "scheduled"
    assert management.started[0]["batch_limit"] == 5
    assert result["started"] == [
        {"decision": result["due"][0], "run": {"runId": "scheduled-run-1", "triggerType": "scheduled"}}
    ]
    assert [decision["syncName"] for decision in result["decisions"]] == ["ignored", "orders_hourly"]
    assert result["decisions"][0]["reason"] == "schedule_paused"


def test_source_scheduler_service_keeps_non_terminal_workflow_as_active_run() -> None:
    repo = _SourceRepo([{"id": "run-active", "status": "running", "workflow_run_id": "workflow-1"}])
    runtime_repo = _RuntimeRepo({"status": "running", "output": {"committedVersionId": "version-1"}})
    management = _Management()
    service = _service(repo, runtime_repo, management, _Policy())

    result = service.run_due_managed_syncs(
        ctx=RequestContext(tenant_id="tenant-a"),
        now=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
    )

    assert repo.updated == []
    assert management.started == []
    assert next(item for item in result["skipped"] if item["syncName"] == "orders_hourly")["reason"] == (
        "active_run_in_progress"
    )


def test_source_scheduler_service_records_failed_workflow_error_and_validates_max_runs() -> None:
    repo = _SourceRepo([{"id": "run-old", "status": "running", "workflow_run_id": "workflow-1"}])
    runtime_repo = _RuntimeRepo({"status": "failed", "output": {"committedVersionId": 123}, "error": {"code": "boom"}})
    service = _service(repo, runtime_repo, _Management(), _Policy())

    result = service.run_due_managed_syncs(
        ctx=RequestContext(tenant_id="tenant-a"),
        now=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        max_runs=1,
    )

    assert repo.updated[0]["status"] == "failed"
    assert repo.updated[0]["dataset_version_id"] is None
    assert repo.updated[0]["error"] == {"code": "boom"}
    assert isinstance(service.runtime_service, _RuntimeService)
    assert service.runtime_service.audits[0]["after_ref"] == {
        "status": "failed",
        "workflowRunId": "workflow-1",
    }
    assert len(result["started"]) == 1
    with pytest.raises(ValidationFailed, match="between 1 and 500"):
        service.preview_due_managed_syncs(ctx=RequestContext(), max_runs=0)
    with pytest.raises(ValidationFailed, match="between 1 and 500"):
        service.run_due_managed_syncs(ctx=RequestContext(), max_runs=501)
