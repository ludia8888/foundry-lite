from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

import foundry_lite.application.services.pipeline_run_recovery as pipeline_run_recovery
import pytest
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineExecutionLeaseLost,
    is_stale_pipeline_execution,
    pipeline_execution_heartbeat,
    replayed_pipeline_run_action,
)
from foundry_lite.domain.context import RequestContext


def test_only_expired_execution_lease_is_stale() -> None:
    now = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)
    long_running_with_live_lease = {
        "status": "executing",
        "started_at": (now - timedelta(hours=2)).isoformat(),
        "execution_lease_token": "live-lease",
        "execution_lease_expires_at": (now + timedelta(minutes=1)).isoformat(),
    }
    expired = {
        "status": "executing",
        "execution_lease_token": "expired-lease",
        "execution_lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
    }
    legacy_without_lease = {"status": "executing", "started_at": (now - timedelta(days=1)).isoformat()}

    assert is_stale_pipeline_execution(long_running_with_live_lease, now=now) is False
    assert is_stale_pipeline_execution(expired, now=now) is True
    assert is_stale_pipeline_execution(legacy_without_lease, now=now) is False
    assert replayed_pipeline_run_action(expired) == "fail_stale"


def test_queued_replay_executes_while_terminal_replay_only_reads() -> None:
    assert replayed_pipeline_run_action({"status": "running"}) == "execute"
    assert replayed_pipeline_run_action({"status": "succeeded"}) == "read"


def test_execution_heartbeat_renews_the_durable_lease(monkeypatch) -> None:
    renewed = Event()
    repository = _LeaseRenewalRepository(renewed)
    monkeypatch.setattr(pipeline_run_recovery, "_HEARTBEAT_INTERVAL_SECONDS", 0.001)
    row = cast(PipelineRunRow, {"id": "run-a", "execution_lease_token": "lease-a"})

    with pipeline_execution_heartbeat(
        cast(TransactionManager, _TransactionManager()),
        cast(PipelineRepository, repository),
        RequestContext(tenant_id="tenant-a"),
        row,
    ):
        assert renewed.wait(timeout=1)

    assert repository.tokens
    assert set(repository.tokens) == {"lease-a"}


def test_execution_heartbeat_fences_commits_immediately_after_renewal_loss(monkeypatch) -> None:
    attempted = Event()
    repository = _LostLeaseRepository(attempted)
    monkeypatch.setattr(pipeline_run_recovery, "_HEARTBEAT_INTERVAL_SECONDS", 0.001)
    row = cast(PipelineRunRow, {"id": "run-lost", "execution_lease_token": "lease-lost"})

    with pytest.raises(PipelineExecutionLeaseLost, match="lost before commit"):
        with pipeline_execution_heartbeat(
            cast(TransactionManager, _TransactionManager()),
            cast(PipelineRepository, repository),
            RequestContext(tenant_id="tenant-a"),
            row,
        ) as guard:
            assert attempted.wait(timeout=1)
            guard.require_active()


class _TransactionManager:
    def begin(self) -> "_Transaction":
        return _Transaction()


class _Transaction:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


class _LeaseRenewalRepository:
    def __init__(self, renewed: Event) -> None:
        self._renewed = renewed
        self.tokens: list[str] = []

    def renew_run_execution_lease(self, **kwargs: object) -> PipelineRunRow:
        self.tokens.append(str(kwargs["execution_lease_token"]))
        self._renewed.set()
        return cast(PipelineRunRow, {"id": kwargs["run_id"], "status": "executing"})


class _LostLeaseRepository:
    def __init__(self, attempted: Event) -> None:
        self._attempted = attempted

    def renew_run_execution_lease(self, **_kwargs: object) -> None:
        self._attempted.set()
        return None
