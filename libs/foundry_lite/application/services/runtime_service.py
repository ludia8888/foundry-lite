from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    AuditEventRecord,
    LineageEdgeRecord,
    LineageEdgeRow,
    OutboxEventRecord,
    RuntimeJsonObject,
    RuntimeLookupTable,
    RuntimeRepository,
    RuntimeRetryPlan,
    RuntimeRetryResult,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunDetail,
    RuntimeRunLink,
    RuntimeRunQueryResult,
    RuntimeRunSnapshot,
    TransactionContext,
)
from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.primitives import (
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_run_paging import OPERATIONS_RUN_DEFAULT_LIMIT, query_runtime_run_page
from foundry_lite.application.services.runtime_run_queries import (
    correlation_id,
    error_message,
    optional_run_type,
    related_action_writebacks,
    related_audit,
    related_object_edits,
    related_outbox,
    required_run_type,
    resource_ids_for_lineage,
    row_for_detail,
    row_references,
    row_status,
    run_investigation,
    source_run_chain,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    FoundryLiteError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)


def _base_error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, AdapterError):
        return adapter_failure_payload(exc)
    if isinstance(exc, FoundryLiteError):
        return {
            "type": exc.code,
            "message": str(exc),
            "details": exc.details,
        }
    return {"type": exc.__class__.__name__, "message": str(exc), "details": {}}


def _trace_correlation_id(
    ctx: RequestContext | None,
    run_id: str | None,
    correlation_id: str | None,
) -> str | None:
    if correlation_id is not None:
        return correlation_id
    if run_id is not None:
        return run_id
    if ctx is not None:
        return ctx.request_id
    return None


def _error_trace(
    ctx: RequestContext | None,
    *,
    run_id: str | None,
    correlation_id: str | None,
    adapter: str | None,
) -> dict[str, str]:
    trace: dict[str, str] = {}
    if ctx is not None:
        trace.update(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.actor_user_id,
            request_id=ctx.request_id,
        )
    if run_id is not None:
        trace["run_id"] = run_id
    resolved_correlation_id = _trace_correlation_id(ctx, run_id, correlation_id)
    if resolved_correlation_id is not None:
        trace["correlation_id"] = resolved_correlation_id
    if adapter is not None:
        trace["adapter"] = adapter
    return trace


class RuntimeService(CoreService):
    required_dependencies = ("engine", "policy", "runtime_repository")
    required_collaborators = ()

    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[LineageEdgeRow]:
        ctx = ctx or RequestContext()
        return self.runtime_repository.lineage_for_resource(tenant_id=ctx.tenant_id, resource_id=resource_id)

    def source_run_chain(
        self,
        source_dataset_version_id: str,
        *,
        object_type_api_name: str,
        ctx: RequestContext | None = None,
    ) -> list[RuntimeRunLink]:
        ctx = ctx or RequestContext()
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        lineage_rows = self.runtime_repository.lineage_for_resource(
            tenant_id=ctx.tenant_id,
            resource_id=source_dataset_version_id,
        )
        return source_run_chain(
            snapshot,
            source_dataset_version_id=source_dataset_version_id,
            object_type_api_name=object_type_api_name,
            lineage_rows=lineage_rows,
        )

    def list_runs(self, *, ctx: RequestContext | None = None) -> RuntimeRunSnapshot:
        ctx = ctx or RequestContext()
        return self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)

    def query_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        run_type: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = OPERATIONS_RUN_DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> RuntimeRunQueryResult:
        ctx = ctx or RequestContext()
        parsed_type = optional_run_type(run_type)
        return query_runtime_run_page(
            self.runtime_repository,
            tenant_id=ctx.tenant_id,
            run_type=parsed_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )

    def run_detail(self, run_type: str, run_id: str, *, ctx: RequestContext | None = None) -> RuntimeRunDetail:
        ctx = ctx or RequestContext()
        parsed_type = required_run_type(run_type)
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        row = row_for_detail(snapshot, parsed_type, run_id)
        outbox_events = related_outbox(snapshot, row)
        audit_events = related_audit(snapshot, row)
        object_edits = related_object_edits(snapshot, row)
        action_writebacks = related_action_writebacks(snapshot, row)
        lineage_edges = self._lineage_edges_for_row(ctx, row)
        return {
            "runType": parsed_type,
            "runId": run_id,
            "row": row,
            "status": row_status(row),
            "errorMessage": error_message(row.get("error")),
            "error": row.get("error"),
            "correlationId": correlation_id(row),
            "references": row_references(row),
            "investigation": run_investigation(
                row,
                parsed_type,
                related_outbox_count=len(outbox_events),
                related_audit_count=len(audit_events),
                related_object_edit_count=len(object_edits),
                related_action_writeback_count=len(action_writebacks),
                lineage_edge_count=len(lineage_edges),
            ),
            "relatedOutboxEvents": outbox_events,
            "relatedAuditEvents": audit_events,
            "relatedObjectEdits": object_edits,
            "relatedActionWritebacks": action_writebacks,
            "lineageEdges": lineage_edges,
        }

    def dead_letter_event_retry_plan(
        self,
        event_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> RuntimeRetryPlan:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            return _dead_letter_retry_plan(self.runtime_repository, conn, ctx, event_id)

    def retry_dead_letter_event(self, event_id: str, *, ctx: RequestContext | None = None) -> RuntimeRetryResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "operations:retry", "dead_letter_event", event_id)
        with self.engine.begin() as conn:
            plan = _dead_letter_retry_plan(self.runtime_repository, conn, ctx, event_id)
            outbox_event_id = plan["outboxEventId"]
            outbox = self.runtime_repository.update_outbox_event_for_retry(
                transaction=conn, tenant_id=ctx.tenant_id, event_id=outbox_event_id
            )
            if outbox is None:
                raise NotFound("source outbox event not found", details={"event_id": outbox_event_id})
            deleted = self.runtime_repository.delete_dead_letter_event(
                transaction=conn, tenant_id=ctx.tenant_id, event_id=event_id
            )
            if not deleted:
                raise NotFound("dead-letter event not found", details={"event_id": event_id})
            self._audit_dlq_retry(
                conn,
                ctx,
                event_id=event_id,
                outbox_event_id=outbox_event_id,
                event_type=plan["eventType"],
            )
        return {**plan, "status": "pending"}

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

    def _select_by_id(self, conn: TransactionContext, table: RuntimeLookupTable, row_id: str) -> RuntimeRow | None:
        return self.runtime_repository.row_by_id(transaction=conn, table=table, row_id=row_id)

    def _rows_for_tenant(
        self, conn: TransactionContext, table: RuntimeRowsTable, ctx: RequestContext
    ) -> Sequence[RuntimeRow]:
        return self.runtime_repository.rows_for_tenant(transaction=conn, table=table, tenant_id=ctx.tenant_id)

    def _lineage_edges_for_row(self, ctx: RequestContext, row: RuntimeRow) -> list[LineageEdgeRow]:
        edges: list[LineageEdgeRow] = []
        seen: set[str] = set()
        for resource_id in resource_ids_for_lineage(row):
            for edge in self.runtime_repository.lineage_for_resource(tenant_id=ctx.tenant_id, resource_id=resource_id):
                edge_id = edge["id"]
                if edge_id not in seen:
                    seen.add(edge_id)
                    edges.append(edge)
        return edges

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
    ) -> None:
        self.runtime_repository.insert_outbox_event(
            transaction=conn,
            record=OutboxEventRecord(
                event_id=_new_id("outbox"),
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

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> Mapping[str, object]:
        payload = _base_error_payload(exc)
        trace = _error_trace(ctx, run_id=run_id, correlation_id=correlation_id, adapter=adapter)
        if trace:
            payload["trace"] = trace
        return payload

    def _audit_dlq_retry(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_id: str,
        outbox_event_id: str,
        event_type: str,
    ) -> None:
        self._audit(
            conn,
            ctx,
            event_type="dead_letter_event.retry_requested",
            resource_type="dead_letter_event",
            resource_id=event_id,
            action="operations:retry",
            before_ref={"deadLetterEventId": event_id, "eventType": event_type},
            after_ref={"outboxEventId": outbox_event_id, "status": "pending"},
            correlation_id=ctx.request_id,
        )


def _required_string(row: RuntimeRow, key: str, event_id: str) -> str:
    value = row.get(key)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("dead-letter event is not retryable", details={"event_id": event_id, "field": key})


def _dead_letter_retry_plan(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    event_id: str,
) -> RuntimeRetryPlan:
    dead_letter = runtime_repository.dead_letter_event_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        event_id=event_id,
    )
    if dead_letter is None:
        raise NotFound("dead-letter event not found", details={"event_id": event_id})
    outbox_event_id = _required_string(dead_letter, "source_event_id", event_id)
    _require_source_outbox(runtime_repository, conn, ctx, outbox_event_id)
    return {
        "deadLetterEventId": event_id,
        "outboxEventId": outbox_event_id,
        "eventType": _required_string(dead_letter, "event_type", event_id),
        "payload": _required_payload(dead_letter, event_id),
    }


def _require_source_outbox(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    event_id: str,
) -> None:
    outbox_rows = runtime_repository.rows_for_tenant(transaction=conn, table="outbox_events", tenant_id=ctx.tenant_id)
    if not any(row.get("id") == event_id for row in outbox_rows):
        raise NotFound("source outbox event not found", details={"event_id": event_id})


def _required_payload(row: RuntimeRow, event_id: str) -> RuntimeJsonObject:
    value = row.get("payload")
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise ValidationFailed("dead-letter event is not retryable", details={"event_id": event_id, "field": "payload"})
