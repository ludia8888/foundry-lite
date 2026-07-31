"""Real before-commit external writeback for ontology actions (L8).

This runs the action's external side effect through a real ``ExternalWritebackAdapter`` (e.g. the
S3/MinIO adapter) instead of the ``simulate_writeback_*`` flags, and maps the vendor reach-out's
three shapes onto the existing outcome_unknown / compensation recording paths:

- ``write`` returns AMBIGUOUS (a timeout/connection error) -> ``outcome_unknown`` (NOT failure): the
  write may have landed, so a replay is idempotent and a later ``remote_lookup`` resolves it.
- ``write`` returns LANDED and the local mutation then fails -> ``compensation_required`` (the
  external side effect is already live, so a silent local-only rollback would diverge state).
- ``write`` returns LANDED and the local mutation succeeds -> the action commits normally.

``remote_lookup`` (an idempotent HEAD-style re-read keyed by the action idempotency key) lets the
reconciliation workflow resolve an outcome_unknown against the *real* external system instead of an
operator-provided remote_status.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackAdapter,
    ExternalWriteTarget,
    WriteReceipt,
)
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.action_writebacks import ActionWritebackRecorder
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
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

    def write(self, command: ActionApplyCommand) -> WriteReceipt:
        """Phase 2: perform the real external write with NO DB connection held.

        Returns a ``WriteReceipt`` (LANDED / AMBIGUOUS). A hard, non-timeout adapter error propagates:
        the run stays committed as ``external_pending`` and the recovery sweep resolves it by HEAD.
        """
        uri = command.external_writeback_uri
        if uri is None:
            raise InvariantViolation("real external writeback requires a target uri")
        target = ExternalWriteTarget(uri=uri, idempotency_key=command.idempotency_key)
        return self.adapter.write(target, {"params": dict(command.params)})

    def record_outcome_unknown(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
    ) -> ExternalOutcomeUnknown:
        """AMBIGUOUS: the write may have landed, so record ``outcome_unknown`` (never a guaranteed failure)."""
        return self._recorder().outcome_unknown_before_commit(
            conn,
            ctx,
            action_run_id,
            command.idempotency_key,
            command.request_fingerprint,
            external_writeback_uri=command.external_writeback_uri,
        )

    def record_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
        receipt: WriteReceipt,
    ) -> None:
        """LANDED: record the succeeded writeback (the run terminal transition rides on the local commit)."""
        self._record_succeeded(conn, ctx, action_run_id, command, receipt)

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
