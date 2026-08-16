from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, cast

import pytest
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.pipeline_execution_repository import PipelineRunRow
from foundry_lite.application.ports.pipeline_repository import PipelineRepository
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.pipeline_run_service import PipelineRunService
from foundry_lite.application.services.pipeline_run_unknown_commit_recovery import (
    PipelineUnknownCommitResolution,
    _abort_open_transaction,
    _persist_resolution,
    _record_reconciliation_deferred,
    _recovered_outputs,
    _transaction_statuses,
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
        self.audit_payloads: list[dict[str, object]] = []
        self.should_fail_audit = False

    def _audit(self, *_args: object, **_kwargs: object) -> None:
        if self.should_fail_audit:
            raise RuntimeError("audit password=must-not-leak")
        self.audit_calls += 1
        self.audit_payloads.append(dict(_kwargs))

    def _error_payload(self, exc: Exception, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"type": type(exc).__name__, "message": "safe reconciliation failure"}


class _FailingMediaRepository:
    def transaction_by_id(self, **_kwargs: object) -> object:
        raise RuntimeError("database password=must-not-leak")


class _FailingAbortService:
    def abort(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("adapter token=must-not-leak")


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


def test_pipeline_run_service_does_not_report_an_unresolved_commit_as_reconciled(
    monkeypatch: Any,
) -> None:
    service = object.__new__(PipelineRunService)
    for name in (
        "engine",
        "pipeline_repository",
        "pipeline_execution_repository",
        "dataset_transaction_repository",
        "media_repository",
        "media_transaction_service",
        "runtime_service",
    ):
        setattr(service, name, object())
    monkeypatch.setattr(
        "foundry_lite.application.services.pipeline_run_unknown_commit_recovery.has_unknown_commit_output",
        lambda _row: True,
    )
    monkeypatch.setattr(
        "foundry_lite.application.services.pipeline_run_unknown_commit_recovery.reconcile_unknown_commit_outputs",
        lambda *_args: False,
    )

    assert service.reconcile_unknown_commit_output_for_run(_CTX, _run_row(completed_at=_CTX.request_id)) is False


def test_unknown_commit_status_read_failure_records_deferred_audit() -> None:
    runtime = _RecordingRuntime()

    statuses = _transaction_statuses(
        _TransactionManager(),
        cast(MediaRepository, _FailingMediaRepository()),
        cast(RuntimeEvidenceBoundary, runtime),
        _CTX,
        _run_row(completed_at=_CTX.request_id),
        ("media-tx-1",),
    )

    assert statuses is None
    assert runtime.audit_payloads[0]["event_type"] == "pipeline.commit_outcome_reconciliation_deferred"
    assert runtime.audit_payloads[0]["decision"] == "deny"
    after_ref = cast(dict[str, object], runtime.audit_payloads[0]["after_ref"])
    assert after_ref["stage"] == "load_transaction_statuses"
    assert "must-not-leak" not in str(after_ref)


def test_unknown_commit_abort_and_output_read_failures_record_their_stage(monkeypatch: Any) -> None:
    runtime = _RecordingRuntime()
    row = _run_row(completed_at=_CTX.request_id)
    manager = _TransactionManager()

    aborted = _abort_open_transaction(
        manager,
        cast(MediaTransactionService, _FailingAbortService()),
        cast(RuntimeEvidenceBoundary, runtime),
        _CTX,
        row,
        "media-tx-1",
    )
    monkeypatch.setattr(
        "foundry_lite.application.services.pipeline_run_unknown_commit_recovery.committed_pipeline_outputs",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("storage secret=must-not-leak")),
    )
    recovered = _recovered_outputs(
        manager,
        cast(Any, object()),
        cast(Any, object()),
        cast(MediaRepository, object()),
        cast(RuntimeEvidenceBoundary, runtime),
        _CTX,
        str(row["id"]),
        row,
    )

    assert aborted is False
    assert recovered is None
    stages = [cast(dict[str, object], item["after_ref"])["stage"] for item in runtime.audit_payloads]
    assert stages == ["abort_open_transaction", "load_committed_outputs"]
    abort_ref = cast(dict[str, object], runtime.audit_payloads[0]["after_ref"])
    assert abort_ref["mediaTransactionId"] == "media-tx-1"
    assert "must-not-leak" not in str(runtime.audit_payloads)


def test_unknown_commit_deferred_audit_failure_emits_safe_structured_log(caplog: pytest.LogCaptureFixture) -> None:
    runtime = _RecordingRuntime()
    runtime.should_fail_audit = True

    with caplog.at_level(logging.ERROR):
        _record_reconciliation_deferred(
            _TransactionManager(),
            cast(RuntimeEvidenceBoundary, runtime),
            _CTX,
            _run_row(completed_at=_CTX.request_id),
            "load_transaction_statuses",
            RuntimeError("database password=must-not-leak"),
        )

    assert "pipeline.commit_outcome_reconciliation_evidence_failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text


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
