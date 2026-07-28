from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from foundry_lite.application.ports.pipeline_execution_repository import PipelinePreviewRunRow
from foundry_lite.application.services.pipeline_preview_recovery import (
    PipelinePreviewExecutionLeaseGuard,
    PipelinePreviewExecutionLeaseLost,
    PipelinePreviewRecoveryCursor,
    new_pipeline_preview_execution_lease,
    pipeline_preview_execution_context,
    pipeline_preview_lease_claim_values,
    pipeline_preview_lease_reclaim_values,
    pipeline_preview_utc_now,
    recoverable_pipeline_previews,
    recovered_pipeline_preview_context,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.tenant_context import current_tenant_id


class _TransactionManager:
    def __init__(self) -> None:
        self.tenant_ids: list[str | None] = []

    @contextmanager
    def begin(self):
        self.tenant_ids.append(current_tenant_id())
        yield object()


class _Repository:
    def __init__(self, *, is_renewed: bool) -> None:
        self.is_renewed = is_renewed
        self.calls: list[dict[str, object]] = []

    def renew_preview_execution_lease(self, **kwargs: object) -> PipelinePreviewRunRow | None:
        self.calls.append(dict(kwargs))
        return _row() if self.is_renewed else None


class _MetadataRepository:
    def list_tenant_ids(self) -> list[str]:
        return ["tenant-a", "tenant-b"]


class _RecoveryRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, int]] = []

    def recoverable_previews(self, *, limit: int, **_kwargs: object) -> list[PipelinePreviewRunRow]:
        tenant_id = current_tenant_id()
        self.calls.append((tenant_id, limit))
        return [_recovery_row(str(tenant_id), index) for index in range(limit)]


def _row() -> PipelinePreviewRunRow:
    return {
        "id": "preview-1",
        "tenant_id": "tenant-a",
        "pipeline_id": "pipeline-1",
        "branch_id": "branch-1",
        "status": "RUNNING",
        "graph": {},
        "graph_fingerprint": "graph-fp",
        "target_node_id": None,
        "limits": {},
        "outputs": [],
        "artifacts": [],
        "idempotency_key": "preview-key",
        "request_fingerprint": "request-fp",
        "is_commit_forbidden": True,
        "execution_context": {
            "actorUserId": "user-a",
            "roles": ["admin", "data_engineer"],
            "applicationId": "app-a",
            "clientId": "client-a",
            "tokenScopes": ["pipeline:write"],
        },
        "execution_lease_token": "lease-a",
        "execution_lease_expires_at": "2026-07-28T00:02:00Z",
        "execution_heartbeat_at": "2026-07-28T00:00:00Z",
        "cancel_requested_at": None,
        "error": None,
        "created_by": "user-a",
        "created_at": "2026-07-28T00:00:00Z",
        "started_at": "2026-07-28T00:00:00Z",
        "completed_at": None,
    }


def _recovery_row(tenant_id: str, index: int) -> PipelinePreviewRunRow:
    row = _row()
    row["id"] = f"preview-{tenant_id}-{index}"
    row["tenant_id"] = tenant_id
    return row


def test_preview_recovery_round_trips_the_original_caller_security_context() -> None:
    original = RequestContext(
        tenant_id="tenant-a",
        actor_user_id="user-a",
        request_id="req-original",
        roles=("admin", "data_engineer"),
        application_id="app-a",
        client_id="client-a",
        token_scopes=("pipeline:write",),
    )
    row = _row()
    row["execution_context"] = pipeline_preview_execution_context(original)

    recovered = recovered_pipeline_preview_context(row)

    assert recovered.tenant_id == original.tenant_id
    assert recovered.actor_user_id == original.actor_user_id
    assert recovered.roles == original.roles
    assert recovered.application_id == original.application_id
    assert recovered.client_id == original.client_id
    assert recovered.token_scopes == original.token_scopes
    assert recovered.request_id == "req-pipeline-preview-recovery:preview-1"


def test_legacy_preview_context_recovers_with_least_privileged_writer_role() -> None:
    row = _row()
    row["execution_context"] = {}

    recovered = recovered_pipeline_preview_context(row)

    assert recovered.actor_user_id == "user-a"
    assert recovered.roles == ("data_engineer",)


def test_preview_lease_guard_renews_and_fails_closed_after_ownership_loss() -> None:
    repository = _Repository(is_renewed=True)
    transaction_manager = _TransactionManager()
    guard = PipelinePreviewExecutionLeaseGuard(
        transaction_manager,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        RequestContext(tenant_id="tenant-a"),
        _row(),
    )

    guard.require_active()

    assert repository.calls[0]["execution_lease_token"] == "lease-a"
    assert transaction_manager.tenant_ids == ["tenant-a"]
    repository.is_renewed = False
    with pytest.raises(PipelinePreviewExecutionLeaseLost, match="lease was lost"):
        guard.require_active()
    with pytest.raises(PipelinePreviewExecutionLeaseLost):
        guard.raise_if_failed()


def test_preview_lease_values_share_one_timestamp_and_bound_expiry() -> None:
    lease = new_pipeline_preview_execution_lease(now=datetime(2026, 7, 28, tzinfo=UTC))
    claim = pipeline_preview_lease_claim_values()
    reclaim = pipeline_preview_lease_reclaim_values()

    assert lease.heartbeat_at == "2026-07-28T00:00:00.000000Z"
    assert lease.expires_at == "2026-07-28T00:02:00.000000Z"
    assert claim["started_at"] == claim["execution_heartbeat_at"]
    assert reclaim["reclaim_before"] == reclaim["execution_heartbeat_at"]
    assert claim["execution_lease_token"]
    assert reclaim["execution_lease_token"]


def test_preview_recovery_timestamps_are_fixed_width_utc_and_lexically_ordered() -> None:
    as_of = pipeline_preview_utc_now()
    lease = new_pipeline_preview_execution_lease(
        now=datetime.fromisoformat(as_of.replace("Z", "+00:00")),
    )

    assert as_of.endswith("Z")
    assert datetime.fromisoformat(as_of.replace("Z", "+00:00")).utcoffset() == timedelta(0)
    assert len(as_of) == len(lease.expires_at)
    assert as_of < lease.expires_at


def test_preview_recovery_rotates_and_reserves_a_fair_tenant_share() -> None:
    transaction_manager = _TransactionManager()
    repository = _RecoveryRepository()
    cursor = PipelinePreviewRecoveryCursor()

    first = recoverable_pipeline_previews(
        transaction_manager,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        _MetadataRepository(),  # type: ignore[arg-type]
        cursor,
        as_of="2026-07-28T00:00:00.000000Z",
        limit=4,
    )
    first_calls = list(repository.calls)
    repository.calls.clear()
    second = recoverable_pipeline_previews(
        transaction_manager,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        _MetadataRepository(),  # type: ignore[arg-type]
        cursor,
        as_of="2026-07-28T00:01:00.000000Z",
        limit=1,
    )

    assert first_calls == [("tenant-a", 2), ("tenant-b", 2)]
    assert {row["tenant_id"] for row in first} == {"tenant-a", "tenant-b"}
    assert repository.calls == [("tenant-b", 1)]
    assert [row["tenant_id"] for row in second] == ["tenant-b"]
