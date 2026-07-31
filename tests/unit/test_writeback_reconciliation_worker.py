from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.action_repository import ActionRepository, ObjectEditRecord
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackPayload,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
    WriteReceipt,
)
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalRetryableWriteback,
    ExternalSystemError,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.writeback_reconciliation import (
    WritebackReconciliationWorkerConfig,
    WritebackReconciliationWorkerRuntime,
    build_writeback_reconciliation_runtime,
    config_from_env,
    main,
    run_writeback_reconciliation_batches,
)

from tests.conftest import prepare_indexed_demo


class _MemoryExternalWritebackAdapter:
    profile_name = "memory-external-writeback"

    def __init__(self, write_status: RemoteOutcomeStatus) -> None:
        self.write_status = write_status
        self.targets: dict[str, ExternalWriteTarget] = {}

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del payload
        self.targets[target.idempotency_key] = target
        if self.write_status is RemoteOutcomeStatus.AMBIGUOUS:
            return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS)
        return WriteReceipt(
            status=RemoteOutcomeStatus.LANDED,
            remote_resource_id=f"remote-{target.idempotency_key}",
        )

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        self.targets[target.idempotency_key] = target
        return RemoteOutcome(
            status=RemoteOutcomeStatus.LANDED,
            remote_resource_id=f"remote-{target.idempotency_key}",
        )


class _StrandingExternalWritebackAdapter:
    """``write()`` raises a hard (non-timeout) error, so the run strands committed as ``external_pending``.

    ``remote_lookup()`` then reports the configured outcome (or raises), driving the recovery sweep down
    each branch: LANDED -> apply+succeed, ABSENT -> fail, AMBIGUOUS -> outcome_unknown, raise -> retry-later.
    """

    profile_name = "stranding-external-writeback"

    def __init__(self, *, lookup: RemoteOutcome | None = None, lookup_error: Exception | None = None) -> None:
        self.lookup = lookup
        self.lookup_error = lookup_error
        self.write_calls = 0

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del target, payload
        self.write_calls += 1
        raise RuntimeError("external system crashed mid-write")

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        if self.lookup_error is not None:
            raise self.lookup_error
        if self.lookup is not None:
            return self.lookup
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"remote-{target.idempotency_key}")


class _InjectedLocalCommitFailure(RuntimeError):
    pass


class _LocalCommitFailingActionRepository:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.enabled = True

    def __getattr__(self, name: str) -> object:
        return getattr(self.delegate, name)

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        if self.enabled:
            raise _InjectedLocalCommitFailure("injected local commit failure after external write landed")
        self.delegate.insert_object_edit(transaction=transaction, record=record)


def test_bounded_writeback_recovery_resolves_outcome_unknown_and_audits(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.AMBIGUOUS)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "auto-recover-outcome-unknown"
    uri = "memory://erp/orders/O-1001"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "remote write may have landed"},
            idempotency_key=idempotency_key,
            external_writeback_uri=uri,
            ctx=ctx,
        )

    before = foundry.operations.list_runs(ctx=ctx)
    pending_writeback = _writeback_for_key(before, idempotency_key)
    assert pending_writeback["status"] == "outcome_unknown"
    assert cast(dict[str, object], pending_writeback["request"])["externalWritebackUri"] == uri

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    assert result["processed"] == 1
    assert result["reconciled"] == 1
    assert result["items"][0]["decision"] == "reconciled"
    assert result["items"][0].get("remoteResourceId") == f"remote-{idempotency_key}"
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["status"] == "APPROVED"
    assert _writeback_for_key(snapshot, idempotency_key)["status"] == "reconciled"
    assert _audit_after_ref(snapshot, "action.writeback.recovery_tick")["reconciled"] == 1


def test_worker_recovers_compensation_required_and_writes_evidence(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    failing_repository = _LocalCommitFailingActionRepository(dependencies.action_repository)
    foundry = FoundryLite(
        dependencies=replace(dependencies, action_repository=cast(ActionRepository, failing_repository))
    )
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.LANDED)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "auto-recover-compensation-required"

    with pytest.raises(ExternalCompensationRequired):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "remote accepted before local commit"},
            idempotency_key=idempotency_key,
            external_writeback_uri="memory://erp/orders/O-1001",
            ctx=ctx,
        )

    failing_repository.enabled = False
    evidence_path = tmp_path / "worker-evidence" / "writeback-reconciliation.json"
    result = run_writeback_reconciliation_batches(
        WritebackReconciliationWorkerConfig(
            storage_root=tmp_path / "unused",
            limit=10,
            max_batches=2,
            max_empty_batches=1,
            evidence_path=evidence_path,
        ),
        runtime=WritebackReconciliationWorkerRuntime(foundry=foundry),
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert result.processed == 1
    assert result.reconciled == 1
    assert result.stop_reason == "empty_batches"
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["status"] == "APPROVED"
    assert evidence["processed"] == 1
    assert evidence["reconciled"] == 1
    assert _writeback_for_key(foundry.operations.list_runs(ctx=ctx), idempotency_key)["status"] == "reconciled"


def test_recovery_skips_unresolved_writeback_without_external_uri(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "auto-recover-skip-missing-uri"

    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "simulated path has no remote lookup target"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert result["items"][0].get("reason") == "missing_external_writeback_uri"
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert _writeback_for_key(snapshot, idempotency_key)["status"] == "outcome_unknown"
    assert _audit_after_ref(snapshot, "action.writeback.recovery_tick")["skipped"] == 1


def test_recovery_marks_retryable_writeback_as_retry_candidate(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "auto-recover-retryable"

    with pytest.raises(ExternalRetryableWriteback):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "external system was not changed"},
            idempotency_key=idempotency_key,
            simulate_writeback_retryable=True,
            ctx=ctx,
        )

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    item = result["items"][0]
    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert item["status"] == "retryable"
    assert item["decision"] == "skipped"
    assert item.get("reason") == "retryable_external_not_changed"
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert _writeback_for_key(snapshot, idempotency_key)["status"] == "retryable"
    assert _audit_after_ref(snapshot, "action.writeback.recovery_tick")["skipped"] == 1


def test_recovery_resolves_stranded_external_pending_run(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _StrandingExternalWritebackAdapter()
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "stranded-external-pending"
    uri = "memory://erp/orders/O-1001"

    # A hard error mid-write (a crash) propagates after the write-ahead marker is already committed.
    with pytest.raises(RuntimeError, match="crashed mid-write"):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "process crashed after the write-ahead commit"},
            idempotency_key=idempotency_key,
            external_writeback_uri=uri,
            ctx=ctx,
        )

    stranded = foundry.operations.list_runs(ctx=ctx)
    stranded_run = _run_for_key(stranded, idempotency_key)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)
    # The run is committed as external_pending (recoverable), the object is untouched, and no terminal
    # writeback exists yet — the phase-3 resolve never ran.
    assert stranded_run["status"] == "external_pending"
    assert before["objectVersion"] == order["objectVersion"]
    assert before["properties"]["status"] == "PENDING"
    assert not any(w["idempotency_key"] == idempotency_key for w in stranded["actionWritebacks"])

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    resolved_run = _run_for_key(snapshot, idempotency_key)
    # The sweep HEADs the external system (LANDED) and drives the stranded run to succeeded, applying the
    # mutation exactly once — the external write is never re-issued (recovery reads, never re-writes).
    assert result["processed"] == 1
    assert result["reconciled"] == 1
    assert result["items"][0]["decision"] == "reconciled"
    assert result["items"][0]["actionRunId"] == stranded_run["id"]
    assert result["items"][0].get("remoteResourceId") == f"remote-{idempotency_key}"
    assert resolved_run["status"] == "succeeded"
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["status"] == "APPROVED"
    assert adapter.write_calls == 1


def test_external_writeback_landed_commits_inline_and_replays(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.LANDED)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "external-landed-inline"
    uri = "memory://erp/orders/O-1001"

    def _apply() -> Mapping[str, Any]:
        return foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "external write landed, local commit succeeds"},
            idempotency_key=idempotency_key,
            external_writeback_uri=uri,
            ctx=ctx,
        )

    result = _apply()

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # The external write landed and the local mutation committed atomically in phase 3.
    assert result["status"] == "succeeded"
    assert result["newObjectVersion"] == order["objectVersion"] + 1
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["status"] == "APPROVED"
    assert _run_for_key(snapshot, idempotency_key)["status"] == "succeeded"
    assert _writeback_for_key(snapshot, idempotency_key)["status"] == "succeeded"
    assert adapter.targets[idempotency_key].uri == uri

    # A replay attaches to the same succeeded run and never issues a second external write.
    replay = _apply()
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "succeeded"
    assert replay["actionRunId"] == result["actionRunId"]
    assert list(adapter.targets) == [idempotency_key]


class _VersionBumpingLandedAdapter:
    """``write()`` LANDS but first runs a callback that moves the target's version, so the inline phase-3
    version check fails -> compensation (the external side effect is live, never a silent local rollback)."""

    profile_name = "version-bumping-landed"

    def __init__(self, on_write: Any) -> None:
        self.on_write = on_write
        self.write_calls = 0

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del payload
        self.write_calls += 1
        self.on_write()
        return WriteReceipt(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"remote-{target.idempotency_key}")

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"remote-{target.idempotency_key}")


def test_external_writeback_landed_requires_compensation_when_target_moved(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    def _move_target() -> None:
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "local mutation moves the target between the write-ahead and the resolve"},
            idempotency_key="inline-target-mover",
            ctx=ctx,
        )

    adapter = _VersionBumpingLandedAdapter(_move_target)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "inline-landed-target-moved"

    with pytest.raises(ExternalCompensationRequired):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "external landed but the local target moved"},
            idempotency_key=idempotency_key,
            external_writeback_uri="memory://erp/orders/O-1001",
            ctx=ctx,
        )

    snapshot = foundry.operations.list_runs(ctx=ctx)
    writebacks = [w for w in snapshot["actionWritebacks"] if w["idempotency_key"] == idempotency_key]
    # The external write landed but the local target moved, so the run requires compensation.
    assert adapter.write_calls == 1
    assert _run_for_key(snapshot, idempotency_key)["status"] == "compensation_required"
    assert any(w["status"] == "compensation_required" for w in writebacks)


class _SelfResolvingLandedAdapter:
    """``write()`` runs the recovery sweep (which resolves the just-committed ``external_pending`` run via a
    LANDED HEAD) before returning the configured receipt, so the inline phase-3 finds the run already
    resolved and returns an idempotent replay (no double mutation) — whether the write LANDED or was ambiguous."""

    profile_name = "self-resolving-landed"

    def __init__(self, on_write: Any, *, write_status: RemoteOutcomeStatus = RemoteOutcomeStatus.LANDED) -> None:
        self.on_write = on_write
        self.write_status = write_status
        self.write_calls = 0
        self.lookup_calls = 0

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del payload
        self.write_calls += 1
        self.on_write()
        if self.write_status is RemoteOutcomeStatus.AMBIGUOUS:
            return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS)
        return WriteReceipt(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"remote-{target.idempotency_key}")

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        self.lookup_calls += 1
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id=f"remote-{target.idempotency_key}")


@pytest.mark.parametrize("write_status", [RemoteOutcomeStatus.LANDED, RemoteOutcomeStatus.AMBIGUOUS])
def test_external_writeback_inline_resolve_replays_when_recovery_wins(
    tmp_path: Path, write_status: RemoteOutcomeStatus
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    def _recover() -> None:
        # The recovery sweep resolves the run committed by phase 1 while the external write is in flight.
        foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    adapter = _SelfResolvingLandedAdapter(_recover, write_status=write_status)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = f"inline-resolve-race-{write_status.value}"

    result = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "recovery resolves the run before the inline phase-3 runs"},
        idempotency_key=idempotency_key,
        external_writeback_uri="memory://erp/orders/O-1001",
        ctx=ctx,
    )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # Recovery applied the mutation during write(); the inline resolve (landed or ambiguous branch) returns
    # an idempotent replay and never applies it a second time.
    assert result.get("idempotentReplay") is True
    assert result["status"] == "succeeded"
    assert _run_for_key(snapshot, idempotency_key)["status"] == "succeeded"
    assert after["objectVersion"] == order["objectVersion"] + 1
    assert after["properties"]["status"] == "APPROVED"


def _apply_stranded_run(foundry: FoundryLite, ctx: Any, order: Mapping[str, Any], idempotency_key: str) -> None:
    with pytest.raises(RuntimeError, match="crashed mid-write"):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "process crashed after the write-ahead commit"},
            idempotency_key=idempotency_key,
            external_writeback_uri="memory://erp/orders/O-1001",
            ctx=ctx,
        )


def test_recovery_fails_stranded_run_when_external_write_absent(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _StrandingExternalWritebackAdapter(lookup=RemoteOutcome(status=RemoteOutcomeStatus.ABSENT))
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "stranded-then-absent"
    _apply_stranded_run(foundry, ctx, order, idempotency_key)

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # The write provably did NOT land, so the stranded run fails and the object is never touched.
    assert result["reconciled"] == 1
    assert result["items"][0]["decision"] == "reconciled"
    assert result["items"][0].get("remoteStatus") == "absent"
    assert _run_for_key(snapshot, idempotency_key)["status"] == "failed"
    assert _writeback_for_key(snapshot, idempotency_key)["status"] == "failed"
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"


def test_recovery_defers_stranded_run_when_remote_lookup_ambiguous(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _StrandingExternalWritebackAdapter(lookup=RemoteOutcome(status=RemoteOutcomeStatus.AMBIGUOUS))
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "stranded-then-ambiguous"
    _apply_stranded_run(foundry, ctx, order, idempotency_key)

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # A still-ambiguous HEAD moves the run to outcome_unknown for the writeback sweep to finish later.
    assert result["skipped"] == 1
    assert result["items"][0]["decision"] == "skipped"
    assert result["items"][0].get("reason") == "still_outcome_unknown"
    assert _run_for_key(snapshot, idempotency_key)["status"] == "outcome_unknown"
    assert _writeback_for_key(snapshot, idempotency_key)["status"] == "outcome_unknown"
    assert after["objectVersion"] == order["objectVersion"]


def test_recovery_leaves_stranded_run_pending_when_remote_lookup_fails(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _StrandingExternalWritebackAdapter(lookup_error=ConnectionError("external source unreachable"))
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "stranded-then-unreachable"
    _apply_stranded_run(foundry, ctx, order, idempotency_key)

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # A transient/unreachable HEAD leaves the run external_pending — recoverable on a later tick.
    assert result["failed"] == 1
    assert result["items"][0]["decision"] == "failed"
    assert _run_for_key(snapshot, idempotency_key)["status"] == "external_pending"
    assert after["objectVersion"] == order["objectVersion"]


def test_recovery_fails_when_target_moved_before_landed_recovery(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _StrandingExternalWritebackAdapter(
        lookup=RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id="remote-moved")
    )
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "stranded-then-target-moved"
    _apply_stranded_run(foundry, ctx, order, idempotency_key)

    # A local action bumps the target version before recovery runs (no external URI -> local path).
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "target moved on before recovery"},
        idempotency_key="local-mover",
        ctx=ctx,
    )

    result = foundry.operations.recover_action_writebacks(limit=10, ctx=ctx)

    snapshot = foundry.operations.list_runs(ctx=ctx)
    # The write landed remotely, but the target version no longer matches, so recovery cannot apply the
    # mutation this tick: the run stays external_pending (recoverable) and the sweep reports it failed.
    assert result["failed"] == 1
    assert result["items"][0]["decision"] == "failed"
    assert _run_for_key(snapshot, idempotency_key)["status"] == "external_pending"


def test_external_writeback_apply_fails_fast_on_version_conflict_without_writing(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.LANDED)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "external-version-conflict"

    with pytest.raises(ConflictDetected):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"] + 5,
            params={"reason": "stale version must fail before any external write"},
            idempotency_key=idempotency_key,
            external_writeback_uri="memory://erp/orders/O-1001",
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # Phase 1 rejects the stale version before phase 2, so the external write is never attempted.
    assert adapter.targets == {}
    assert _run_for_key(snapshot, idempotency_key)["status"] == "conflict"
    assert after["objectVersion"] == order["objectVersion"]


def test_external_writeback_simulated_failure_short_circuits_before_writing(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    adapter = _MemoryExternalWritebackAdapter(RemoteOutcomeStatus.LANDED)
    foundry.actions._action.set_external_writeback_adapter(adapter)
    idempotency_key = "external-simulated-failure"

    with pytest.raises(ExternalSystemError):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "simulated failure precedes the real external write"},
            idempotency_key=idempotency_key,
            simulate_writeback_failure=True,
            external_writeback_uri="memory://erp/orders/O-1001",
            ctx=ctx,
        )

    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    snapshot = foundry.operations.list_runs(ctx=ctx)
    # The simulated before-commit failure short-circuits phase 1, so the real adapter is never called.
    assert adapter.targets == {}
    assert _run_for_key(snapshot, idempotency_key)["status"] == "failed"
    assert after["objectVersion"] == order["objectVersion"]


def test_writeback_reconciliation_worker_config_from_env(tmp_path: Path) -> None:
    config = config_from_env(
        {
            "FOUNDRY_LITE_HOME": str(tmp_path / "flite"),
            "FOUNDRY_LITE_DB_URL": "sqlite:///worker.db",
            "FOUNDRY_LITE_ADAPTER_PROFILE": "fake",
            "FOUNDRY_LITE_EXTERNAL_WRITEBACK_PROFILE": "s3-external-writeback",
            "FOUNDRY_LITE_EXTERNAL_WRITEBACK_BUCKET": "orders-writeback",
            "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_LIMIT": "7",
            "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_MAX_BATCHES": "3",
            "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_MAX_EMPTY_BATCHES": "2",
            "FOUNDRY_LITE_WRITEBACK_RECONCILIATION_EVIDENCE_PATH": str(tmp_path / "evidence.json"),
            "FOUNDRY_LITE_TENANT_ID": "tenant-worker",
            "FOUNDRY_LITE_WORKER_ACTOR_USER_ID": "worker-actor",
            "FOUNDRY_LITE_WORKER_REQUEST_ID": "worker-request",
        }
    )

    assert config.storage_root == tmp_path / "flite"
    assert config.db_url == "sqlite:///worker.db"
    assert config.adapter_profile == "fake"
    assert config.external_writeback_profile == "s3-external-writeback"
    assert config.external_writeback_bucket == "orders-writeback"
    assert config.limit == 7
    assert config.max_batches == 3
    assert config.max_empty_batches == 2
    assert config.evidence_path == tmp_path / "evidence.json"
    assert config.request_context(batch_number=4).request_id == "worker-request:batch-4"


def test_writeback_reconciliation_worker_stops_at_max_batches(tmp_path: Path) -> None:
    result = run_writeback_reconciliation_batches(
        WritebackReconciliationWorkerConfig(
            storage_root=tmp_path / "unused",
            limit=10,
            max_batches=1,
            max_empty_batches=5,
        ),
        runtime=WritebackReconciliationWorkerRuntime(foundry=_FoundryWithOperations()),
    )

    assert result.stop_reason == "max_batches"
    assert result.iterations == 1
    assert result.processed == 2
    assert result.reconciled == 1
    assert result.skipped == 1
    assert result.failed == 0
    assert result.writeback_ids == ("wb-1", "wb-2")


def test_writeback_reconciliation_worker_builds_s3_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_S3_ENDPOINT_URL", "https://s3.example.test")
    monkeypatch.setenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID", "access-key")
    monkeypatch.setenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("FOUNDRY_LITE_S3_REGION", "ap-northeast-2")

    runtime = build_writeback_reconciliation_runtime(
        WritebackReconciliationWorkerConfig(
            storage_root=tmp_path / "flite",
            external_writeback_profile="s3-external-writeback",
            external_writeback_bucket="orders-writeback",
        )
    )

    assert isinstance(runtime.foundry, FoundryLite)


def test_writeback_reconciliation_worker_main_success_and_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence_path = tmp_path / "evidence.json"

    success_code = main(
        [
            "--storage-root",
            str(tmp_path / "success-flite"),
            "--limit",
            "1",
            "--max-batches",
            "1",
            "--max-empty-batches",
            "1",
            "--evidence-path",
            str(evidence_path),
        ]
    )
    success_payload = json.loads(capsys.readouterr().out)
    invalid_profile_code = main(["--storage-root", str(tmp_path / "bad-flite"), "--external-writeback-profile", "bad"])
    invalid_profile_payload = json.loads(capsys.readouterr().out)
    invalid_positive_code = main(["--storage-root", str(tmp_path / "bad-positive"), "--limit", "0"])
    invalid_positive_payload = json.loads(capsys.readouterr().out)

    assert success_code == 0
    assert success_payload["status"] == "STOPPED"
    assert evidence_path.exists()
    assert invalid_profile_code == 1
    assert invalid_profile_payload["type"] == "ValueError"
    assert invalid_profile_payload["trace"]["adapter"] == "writeback_reconciliation_worker"
    assert invalid_positive_code == 1
    assert "must be positive" in invalid_positive_payload["message"]


def test_writeback_reconciliation_worker_rejects_required_context_values() -> None:
    with pytest.raises(ValueError, match="requires tenant_id"):
        WritebackReconciliationWorkerConfig(storage_root=Path("."), tenant_id="").request_context(batch_number=1)
    with pytest.raises(ValueError, match="requires actor_user_id"):
        WritebackReconciliationWorkerConfig(storage_root=Path("."), actor_user_id="").request_context(batch_number=1)
    with pytest.raises(ValueError, match="requires request_id"):
        WritebackReconciliationWorkerConfig(storage_root=Path("."), request_id="").request_context(batch_number=1)


def _writeback_for_key(runs: Mapping[str, object], idempotency_key: str) -> dict[str, object]:
    rows = cast(list[dict[str, object]], runs["actionWritebacks"])
    return next(row for row in rows if row["idempotency_key"] == idempotency_key)


def _run_for_key(runs: Mapping[str, object], idempotency_key: str) -> dict[str, object]:
    rows = cast(list[dict[str, object]], runs["actionRuns"])
    return next(row for row in rows if row["idempotency_key"] == idempotency_key)


def _audit_after_ref(runs: Mapping[str, object], event_type: str) -> dict[str, object]:
    events = cast(list[dict[str, object]], runs["auditEvents"])
    event = next(row for row in reversed(events) if row["event_type"] == event_type)
    return cast(dict[str, object], event["after_ref"])


class _FoundryWithOperations:
    def __init__(self) -> None:
        self.operations = _OperationsBatchStub()


class _OperationsBatchStub:
    def recover_action_writebacks(self, *, ctx: Any, limit: int) -> dict[str, object]:
        del ctx, limit
        return {
            "processed": 2,
            "reconciled": 1,
            "skipped": 1,
            "failed": 0,
            "items": [{"writebackId": "wb-1"}, {"writebackId": "wb-2"}],
        }
