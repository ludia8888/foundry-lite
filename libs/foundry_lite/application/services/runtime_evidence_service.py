"""Runtime evidence writer service for audit, outbox, lineage, and run relations."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    AuditEventRecord,
    LineageEdgeRecord,
    OutboxEventRecord,
    RuntimeRunRelationRecord,
    RuntimeRunType,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.application.services.aip.governed_release_mutation_gate import (
    GovernedReleaseMutationRequirement,
    governed_release_required,
    is_authorized_release_run,
    mutation_requirement,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_error_payloads import require_write_traffic_open, runtime_error_payload
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


class RuntimeEvidenceService(CoreService):
    """Owns durable runtime evidence writes shared by application use cases."""

    required_dependencies = ("ai_run_repository", "engine", "policy", "profile", "runtime_repository")
    required_collaborators = ()
    profile: RuntimeProfile

    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self._require_governed_release_mutation(ctx, operation, resource_type, resource_id)
        require_write_traffic_open(
            self.engine,
            self.runtime_repository,
            ctx,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def _require_governed_release_mutation(
        self,
        ctx: RequestContext,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        requirement = mutation_requirement(operation)
        if requirement is None or not self.profile.is_protected:
            return
        with self.engine.begin() as conn:
            run = self._governed_release_run(conn, ctx)
            is_allowed = is_authorized_release_run(run, ctx, requirement, resource_id)
            if not is_allowed:
                self._audit_governed_release_denial(conn, ctx, operation, resource_type, resource_id, requirement)
        if not is_allowed:
            raise governed_release_required(operation)

    def _governed_release_run(self, conn: TransactionContext, ctx: RequestContext) -> Mapping[str, object] | None:
        run_id = ctx.governed_release_run_id
        if not isinstance(run_id, str) or not run_id:
            return None
        return self.ai_run_repository.execution_run_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=run_id,
        )

    def _audit_governed_release_denial(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        operation: str,
        resource_type: str,
        resource_id: str,
        requirement: GovernedReleaseMutationRequirement,
    ) -> None:
        self._audit(
            conn,
            ctx,
            event_type="governed_release.mutation.denied",
            resource_type=resource_type,
            resource_id=resource_id,
            action=operation,
            decision="deny",
            policy_decision={
                "reason": "governed_release_required",
                "requiredTools": list(requirement.tool_names),
                "runtimeProfile": self.profile.name,
            },
            correlation_id=ctx.request_id,
        )

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action=permission,
                    decision="deny",
                )
            raise

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.runtime_repository.insert_audit_event(
            transaction=conn,
            record=AuditEventRecord(
                event_id=_new_id("audit"),
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.actor_user_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                policy_decision=dict(policy_decision or {}),
                before_ref=dict(before_ref or {}),
                after_ref=dict(after_ref or {}),
                correlation_id=correlation_id or ctx.request_id,
                request_id=ctx.request_id,
                metadata={},
                created_at=_now(),
            ),
        )

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None:
        event_id = _new_id("outbox")
        inserted = self.runtime_repository.insert_outbox_event(
            transaction=conn,
            record=OutboxEventRecord(
                event_id=event_id,
                tenant_id=ctx.tenant_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=dict(payload),
                status="pending",
                attempts=0,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                created_at=_now(),
                published_at=None,
            ),
        )
        return event_id if inserted else None

    def _outbox_event_by_idempotency_key(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        return self.runtime_repository.outbox_event_by_idempotency_key(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
        )

    def _lineage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        relation: str,
        run_id: str,
    ) -> None:
        self.runtime_repository.insert_lineage_edge(
            transaction=conn,
            record=LineageEdgeRecord(
                edge_id=_new_id("lineage"),
                tenant_id=ctx.tenant_id,
                from_resource_type=from_type,
                from_resource_id=from_id,
                to_resource_type=to_type,
                to_resource_id=to_id,
                relation=relation,
                created_by_run_id=run_id,
                created_at=_now(),
            ),
        )

    def _run_relation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        source_run_type: RuntimeRunType,
        source_run_id: str,
        target_run_type: RuntimeRunType,
        target_run_id: str,
        relation: str,
        resource_type: str,
        resource_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        return self.runtime_repository.insert_run_relation(
            transaction=conn,
            record=RuntimeRunRelationRecord(
                relation_id=_new_id("run_relation"),
                tenant_id=ctx.tenant_id,
                source_run_type=source_run_type,
                source_run_id=source_run_id,
                target_run_type=target_run_type,
                target_run_id=target_run_id,
                relation=relation,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=dict(metadata or {}),
                created_at=_now(),
            ),
        )

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> Mapping[str, object]:
        return runtime_error_payload(exc, ctx, run_id=run_id, correlation_id=correlation_id, adapter=adapter)
