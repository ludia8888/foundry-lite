from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    OUTBOX_RETRY_PENDING,
    AuditEventRecord,
    DatasetTransactionRepository,
    LineageEdgeRecord,
    LineageEdgeRow,
    ObservabilityDetectorConfig,
    ObservabilityReport,
    OutboxEventRecord,
    RuntimeJsonObject,
    RuntimeLookupTable,
    RuntimeRetryPlan,
    RuntimeRetryResult,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunDetail,
    RuntimeRunLink,
    RuntimeRunQueryResult,
    RuntimeRunRelationRecord,
    RuntimeRunSnapshot,
    RuntimeRunType,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.observability_detectors import build_observability_report
from foundry_lite.application.services.runtime_detail_payload import (
    dataset_transaction_for_row,
    lineage_edges_for_row,
    quality_evidence_for_transaction,
    run_relations_for_row,
    runtime_run_detail_payload,
)
from foundry_lite.application.services.runtime_error_payloads import (
    dead_letter_retry_plan,
    require_outbox_retry_open,
    require_write_traffic_open,
    runtime_error_payload,
)
from foundry_lite.application.services.runtime_run_paging import OPERATIONS_RUN_DEFAULT_LIMIT, query_runtime_run_page
from foundry_lite.application.services.runtime_run_queries import (
    downstream_impact_graph,
    object_late_data_badge,
    optional_run_type,
    related_action_writebacks,
    related_audit,
    related_object_edits,
    related_outbox,
    required_run_type,
    row_for_detail,
    source_run_chain,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied


def _redact_sensitive(value: object, sensitive: set[str]) -> object:
    if isinstance(value, Mapping):
        return {
            key: "***MASKED***" if key in sensitive else _redact_sensitive(item, sensitive)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item, sensitive) for item in value]
    return value


class RuntimeService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "runtime_repository",
        "dataset_transaction_repository",
        "dataset_quality_repository",
    )
    required_collaborators = ()
    dataset_transaction_repository: DatasetTransactionRepository

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

    def late_data_badge_for_source(
        self,
        source_dataset_version_id: str,
        *,
        object_type_api_name: str,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject | None:
        ctx = ctx or RequestContext()
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        return object_late_data_badge(
            snapshot,
            source_dataset_version_id=source_dataset_version_id,
            object_type_api_name=object_type_api_name,
        )

    def list_runs(self, *, ctx: RequestContext | None = None) -> RuntimeRunSnapshot:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:summary")
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        return cast(RuntimeRunSnapshot, _redact_sensitive(snapshot, self.policy.sensitive_column_names(ctx)))

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
        self.policy.require(ctx, "operations:read:summary")
        parsed_type = optional_run_type(run_type)
        page = query_runtime_run_page(
            self.runtime_repository,
            tenant_id=ctx.tenant_id,
            run_type=parsed_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
        return cast(RuntimeRunQueryResult, _redact_sensitive(page, self.policy.sensitive_column_names(ctx)))

    def observability_report(
        self,
        *,
        ctx: RequestContext | None = None,
        configs: Sequence[ObservabilityDetectorConfig] = (),
        previous_incidents: Sequence[Mapping[str, object]] = (),
        observed_at: str | None = None,
    ) -> ObservabilityReport:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:summary")
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        report = build_observability_report(
            snapshot,
            configs=configs,
            previous_incidents=previous_incidents,
            observed_at=observed_at or _now(),
        )
        return cast(ObservabilityReport, _redact_sensitive(report, self.policy.sensitive_column_names(ctx)))

    def run_detail(self, run_type: str, run_id: str, *, ctx: RequestContext | None = None) -> RuntimeRunDetail:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:detail")
        parsed_type = required_run_type(run_type)
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        row = row_for_detail(snapshot, parsed_type, run_id)
        outbox_events = related_outbox(snapshot, row)
        audit_events = related_audit(snapshot, row)
        object_edits = related_object_edits(snapshot, row)
        action_writebacks = related_action_writebacks(snapshot, row)
        lineage_edges = lineage_edges_for_row(self.runtime_repository, ctx, row)
        run_relations = run_relations_for_row(self.runtime_repository, self.engine, ctx, parsed_type, run_id)
        dataset_transaction = dataset_transaction_for_row(self.dataset_transaction_repository, self.engine, ctx, row)
        quality_check_results, quality_failed_row_samples = quality_evidence_for_transaction(
            self.dataset_quality_repository,
            self.dataset_transaction_repository,
            self.engine,
            ctx,
            dataset_transaction,
        )
        detail = runtime_run_detail_payload(
            parsed_type=parsed_type,
            run_id=run_id,
            row=row,
            outbox_events=outbox_events,
            audit_events=audit_events,
            object_edits=object_edits,
            action_writebacks=action_writebacks,
            run_relations=run_relations,
            lineage_edges=lineage_edges,
            dataset_transaction=dataset_transaction,
            downstream_impact=self._downstream_impact(ctx, row, dataset_transaction, snapshot),
            quality_check_results=quality_check_results,
            quality_failed_row_samples=quality_failed_row_samples,
        )
        return cast(RuntimeRunDetail, _redact_sensitive(detail, self.policy.sensitive_column_names(ctx)))

    def dead_letter_event_retry_plan(
        self,
        event_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> RuntimeRetryPlan:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "operations:retry", "dead_letter_event", event_id)
        with self.engine.begin() as conn:
            self._require_outbox_retry_open(conn, ctx)
            return dead_letter_retry_plan(self.runtime_repository, conn, ctx, event_id)

    def retry_dead_letter_event(self, event_id: str, *, ctx: RequestContext | None = None) -> RuntimeRetryResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "operations:retry", "dead_letter_event", event_id)
        with self.engine.begin() as conn:
            self._require_outbox_retry_open(conn, ctx)
            plan = dead_letter_retry_plan(self.runtime_repository, conn, ctx, event_id)
            outbox_event_id = plan["outboxEventId"]
            outbox = self.runtime_repository.update_outbox_event_for_retry(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=outbox_event_id,
                transition=OUTBOX_RETRY_PENDING,
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
            self._run_relation(
                conn,
                ctx,
                source_run_type="dead_letter",
                source_run_id=event_id,
                target_run_type="outbox",
                target_run_id=outbox_event_id,
                relation="requeued",
                resource_type="outbox_event",
                resource_id=outbox_event_id,
                metadata={"eventType": plan["eventType"]},
            )
        return {**plan, "status": "pending"}

    def _require_outbox_retry_open(self, conn: TransactionContext, ctx: RequestContext) -> None:
        require_outbox_retry_open(self.runtime_repository, conn, ctx)

    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        require_write_traffic_open(
            self.engine,
            self.runtime_repository,
            ctx,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
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

    def _select_by_id(self, conn: TransactionContext, table: RuntimeLookupTable, row_id: str) -> RuntimeRow | None:
        return self.runtime_repository.row_by_id(transaction=conn, table=table, row_id=row_id)

    def _rows_for_tenant(
        self, conn: TransactionContext, table: RuntimeRowsTable, ctx: RequestContext
    ) -> Sequence[RuntimeRow]:
        return self.runtime_repository.rows_for_tenant(transaction=conn, table=table, tenant_id=ctx.tenant_id)

    def _downstream_impact(
        self,
        ctx: RequestContext,
        row: RuntimeRow,
        dataset_transaction: RuntimeRow | None,
        snapshot: RuntimeRunSnapshot,
    ) -> RuntimeJsonObject | None:
        return downstream_impact_graph(
            row,
            dataset_transaction,
            snapshot,
            lambda resource_id: self.runtime_repository.lineage_for_resource(
                tenant_id=ctx.tenant_id,
                resource_id=resource_id,
            ),
        )

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
