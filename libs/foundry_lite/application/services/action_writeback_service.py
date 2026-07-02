"""Action writeback and reconciliation use case service."""

from __future__ import annotations

from foundry_lite.application.action_types import (
    ActionApplyCommand,
    ActionWritebackQueueResult,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
    ActionWritebackRecoveryResult,
)
from foundry_lite.application.services.action_reconciliation import ActionWritebackReconciliationWorkflow
from foundry_lite.application.services.action_workflow import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
    ActionWritebackRecorder,
    ExternalWritebackAdapter,
    RealExternalWritebackRunner,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class ActionWritebackService(CoreService):
    """Own external writeback adapter state and operator reconciliation workflows."""

    required_dependencies = ("engine", "policy", "action_repository")
    required_collaborators = (
        "object_indexing_service",
        "object_records_service",
        "ontology_service",
        "runtime_service",
    )
    object_indexing_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
    runtime_service: ActionRuntimeBoundary
    _external_writeback_adapter: ExternalWritebackAdapter | None = None

    def set_external_writeback_adapter(self, adapter: ExternalWritebackAdapter) -> None:
        self._external_writeback_adapter = adapter

    def reconcile_action_writeback(
        self,
        writeback_id: str,
        *,
        remote_status: str | None = None,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackReconciliationResult:
        ctx = ctx or RequestContext()
        return self._writeback_reconciliation_workflow().reconcile(
            writeback_id,
            remote_status=remote_status,
            remote_resource_id=remote_resource_id,
            external_writeback_uri=external_writeback_uri,
            ctx=ctx,
        )

    def list_unresolved_action_writebacks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackQueueResult:
        ctx = ctx or RequestContext()
        return self._writeback_reconciliation_workflow().list_unresolved(ctx=ctx, status=status, limit=limit)

    def recover_action_writebacks(
        self,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackRecoveryResult:
        ctx = ctx or RequestContext()
        return self._writeback_reconciliation_workflow().recover_unresolved(ctx=ctx, limit=limit)

    def approve_action_writeback_recovery(
        self,
        writeback_id: str,
        *,
        approval_id: str,
        reason: str,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackRecoveryItem:
        ctx = ctx or RequestContext()
        return self._writeback_reconciliation_workflow().approve_recovery(
            writeback_id,
            approval_id=approval_id,
            reason=reason,
            external_writeback_uri=external_writeback_uri,
            ctx=ctx,
        )

    def writeback_recorder(self) -> ActionWritebackRecorder:
        return ActionWritebackRecorder(
            action_repository=self.action_repository,
            runtime_service=self.runtime_service,
        )

    def real_writeback_runner(self, command: ActionApplyCommand) -> RealExternalWritebackRunner | None:
        adapter = self._external_writeback_adapter
        if adapter is None or command.external_writeback_uri is None:
            return None
        return RealExternalWritebackRunner(
            adapter=adapter,
            action_repository=self.action_repository,
            runtime_service=self.runtime_service,
        )

    def _writeback_reconciliation_workflow(self) -> ActionWritebackReconciliationWorkflow:
        return ActionWritebackReconciliationWorkflow(
            engine=self.engine,
            policy=self.policy,
            action_repository=self.action_repository,
            object_indexing_service=self.object_indexing_service,
            object_records_service=self.object_records_service,
            ontology_service=self.ontology_service,
            runtime_service=self.runtime_service,
            external_writeback_adapter=self._external_writeback_adapter,
        )
