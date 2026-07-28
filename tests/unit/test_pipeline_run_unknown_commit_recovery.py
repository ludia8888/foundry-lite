from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

from foundry_lite.application.ports.pipeline_execution_repository import PipelineRunRow
from foundry_lite.application.ports.pipeline_repository import PipelineRepository
from foundry_lite.application.services.pipeline_run_unknown_commit_recovery import (
    PipelineUnknownCommitResolution,
    _persist_resolution,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import PIPELINE_RUN_RECONCILED_PARTIAL
from foundry_lite.domain.context import RequestContext

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="user-data-engineer",
    request_id="req-reconcile-fence",
    roles=("data_engineer",),
)


class _TransactionManager:
    def __init__(self) -> None:
        self.begin_calls = 0

    @contextmanager
    def begin(self) -> Any:
        self.begin_calls += 1
        yield object()


class _FencedRepository:
    def __init__(self) -> None:
        self.expected_completed_at: list[str | None] = []

    def update_run_terminal(self, **kwargs: object) -> PipelineRunRow | None:
        self.expected_completed_at.append(cast(str | None, kwargs["expected_completed_at"]))
        if len(self.expected_completed_at) == 1:
            return _run_row(completed_at="2026-07-28T00:01:00+00:00")
        return None


class _RecordingRuntime:
    def __init__(self) -> None:
        self.audit_calls = 0

    def _audit(self, *_args: object, **_kwargs: object) -> None:
        self.audit_calls += 1


def test_unknown_commit_resolution_rejects_missing_terminal_fence() -> None:
    transaction_manager = _TransactionManager()

    persisted = _persist_resolution(
        transaction_manager,
        cast(PipelineRepository, _FencedRepository()),
        cast(RuntimeEvidenceBoundary, _RecordingRuntime()),
        _CTX,
        _run_row(completed_at=None),
        _resolution(),
    )

    assert persisted is False
    assert transaction_manager.begin_calls == 0


def test_unknown_commit_resolution_audits_only_the_compare_and_swap_winner() -> None:
    transaction_manager = _TransactionManager()
    repository = _FencedRepository()
    runtime = _RecordingRuntime()
    stale_row = _run_row(completed_at="2026-07-28T00:00:00+00:00")

    first = _persist_resolution(
        transaction_manager,
        cast(PipelineRepository, repository),
        cast(RuntimeEvidenceBoundary, runtime),
        _CTX,
        stale_row,
        _resolution(),
    )
    stale = _persist_resolution(
        transaction_manager,
        cast(PipelineRepository, repository),
        cast(RuntimeEvidenceBoundary, runtime),
        _CTX,
        stale_row,
        _resolution(),
    )

    assert first is True
    assert stale is False
    assert transaction_manager.begin_calls == 2
    assert repository.expected_completed_at == [
        "2026-07-28T00:00:00+00:00",
        "2026-07-28T00:00:00+00:00",
    ]
    assert runtime.audit_calls == 1


def _run_row(*, completed_at: str | None) -> PipelineRunRow:
    return {
        "id": "prun-reconcile",
        "tenant_id": _CTX.tenant_id,
        "pipeline_id": "pipeline-demo",
        "version_id": "version-1",
        "status": "partial",
        "idempotency_key": "run-once",
        "request_fingerprint": "fingerprint",
        "plan_fingerprint": "plan-fingerprint",
        "workflow_run_id": None,
        "execution_lease_token": None,
        "execution_lease_expires_at": None,
        "execution_heartbeat_at": None,
        "parameters": {},
        "target_node_ids": None,
        "outputs": [{"nodeId": "output", "status": "COMMIT_OUTCOME_UNKNOWN"}],
        "output_dataset_ref": None,
        "output_version_id": None,
        "timeline": [{"event": "pipeline.run.partial"}],
        "error": {"message": "commit outcome unknown"},
        "created_by": _CTX.actor_user_id,
        "started_at": "2026-07-28T00:00:00+00:00",
        "completed_at": completed_at,
    }


def _resolution() -> PipelineUnknownCommitResolution:
    return PipelineUnknownCommitResolution(
        transition=PIPELINE_RUN_RECONCILED_PARTIAL,
        status="partial",
        outputs=({"nodeId": "output", "status": "FAILED"},),
        output_dataset_ref=None,
        output_version_id=None,
        error={"message": "reconciled"},
    )
