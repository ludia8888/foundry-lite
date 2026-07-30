"""Real before-commit external writeback for ontology actions (L8).

This runs the action's external side effect through a real ``ExternalWritebackAdapter`` (e.g. the
S3/MinIO adapter) instead of the ``simulate_writeback_*`` flags, and maps the vendor reach-out's
three shapes onto the existing outcome_unknown / compensation recording paths:

- ``write`` returns AMBIGUOUS (a timeout/connection error) -> ``outcome_unknown`` (NOT failure): the
  write may have landed, so a replay is idempotent and a later ``remote_lookup`` resolves it.
- ``write`` returns LANDED and the local mutation then fails -> ``compensation_required`` (the
  external side effect is already live, so a silent local-only rollback would diverge state).
- ``write`` returns LANDED and the local mutation succeeds -> the action commits normally.

The external ``write`` is split off from the DB recording on purpose: :meth:`write_external` performs
the remote reach-out with **no DB connection held** (the caller has committed the ``received`` run in
its own short transaction beforehand), and :meth:`record_receipt_before_commit` records the receipt
together with the local mutation in a fresh transaction. Holding a pooled connection open across a
remote call would exhaust the pool under fleet load, so the connection is only taken for the short
local write.

``remote_lookup`` (an idempotent HEAD-style re-read keyed by the action idempotency key) lets the
reconciliation workflow resolve an outcome_unknown against the *real* external system instead of an
operator-provided remote_status.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyOutcome, ActionApplyResponse
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackAdapter,
    ExternalWriteTarget,
    RemoteOutcomeStatus,
    WriteReceipt,
)
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.action_writebacks import ActionWritebackRecorder
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ExternalCompensationRequired,
    InvariantViolation,
)


class LocalCommitFailed(Exception):
    """Internal signal: a real external write LANDED but the local mutation commit failed.

    Carries the ``WriteReceipt`` out of the rolled-back transaction so compensation can be recorded
    in a fresh transaction (the external side effect is already live and must not be silently dropped).
    """

    receipt: WriteReceipt

    def __init__(self, receipt: WriteReceipt) -> None:
        super().__init__("local mutation commit failed after external write landed")
        self.receipt = receipt


@dataclass(frozen=True)
class RealExternalWritebackRunner:
    """Drives the action external side effect through a real ``ExternalWritebackAdapter``."""

    adapter: ExternalWritebackAdapter
    action_repository: ActionRepository
    runtime_service: ActionRuntimeBoundary

    def _recorder(self) -> ActionWritebackRecorder:
        return ActionWritebackRecorder(
            action_repository=self.action_repository,
            runtime_service=self.runtime_service,
            connector_id=self.adapter.profile_name,
            is_simulated=False,
        )

    def write_external(self, command: ActionApplyCommand) -> WriteReceipt:
        """Perform the external side effect with **no DB connection held**.

        The action idempotency key addresses the write, so a replay reaches out to the same target
        (idempotent, not a blind new write). A timeout/connection error returns an AMBIGUOUS receipt
        (outcome_unknown, never a guaranteed failure); a clean rejection raises (the write did not
        land, so the caller records the run as failed rather than losing a phantom external success).
        """
        uri = command.external_writeback_uri
        if uri is None:
            raise InvariantViolation("real external writeback requires a target uri")
        target = ExternalWriteTarget(uri=uri, idempotency_key=command.idempotency_key)
        return self.adapter.write(target, {"params": dict(command.params)})

    def record_receipt_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
        receipt: WriteReceipt,
        *,
        commit: Callable[[], ActionApplyResponse],
    ) -> ActionApplyOutcome:
        """Record the already-issued external receipt, then the local commit, in the caller's fresh tx.

        ``receipt`` comes from :meth:`write_external`, which ran outside any transaction. This maps the
        two landed/ambiguous shapes onto the recording + local-commit paths, holding the connection
        only for the short local write.
        """
        if receipt.status is RemoteOutcomeStatus.AMBIGUOUS:
            # A timeout is outcome_unknown (the write may have landed), never a guaranteed failure.
            unknown = self._recorder().outcome_unknown_before_commit(
                conn,
                ctx,
                action_run_id,
                command.idempotency_key,
                command.request_fingerprint,
                external_writeback_uri=command.external_writeback_uri,
            )
            return ActionApplyOutcome(deferred_error=unknown)
        self._record_succeeded(conn, ctx, action_run_id, command, receipt)
        try:
            return ActionApplyOutcome(response=commit())
        except Exception as exc:
            # The external write already LANDED (proven by the receipt), but the local mutation failed:
            # roll back and require compensation in a fresh transaction (never a silent local rollback).
            raise LocalCommitFailed(receipt) from exc

    def _record_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
        receipt: WriteReceipt,
    ) -> None:
        self._recorder().record(
            conn,
            ctx,
            action_run_id,
            status="succeeded",
            idempotency_key=command.idempotency_key,
            request_hash=command.request_fingerprint,
            response={"status_code": 200, "remote_resource_id": receipt.remote_resource_id},
            external_writeback_uri=command.external_writeback_uri,
        )

    def record_compensation_required(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
        receipt: WriteReceipt,
    ) -> ExternalCompensationRequired:
        """A real external write LANDED but the local mutation failed: compensation is required."""
        self._record_succeeded(conn, ctx, action_run_id, command, receipt)
        return self._recorder().compensation_required_before_commit(
            conn,
            ctx,
            action_run_id,
            command.idempotency_key,
            command.request_fingerprint,
            remote_resource_id=receipt.remote_resource_id,
            external_writeback_uri=command.external_writeback_uri,
        )
