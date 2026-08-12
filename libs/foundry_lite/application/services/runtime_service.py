"""Application service helpers for runtime service workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    OUTBOX_RETRY_PENDING,
    DatasetTransactionRepository,
    LineageEdgeRow,
    RuntimeJsonObject,
    RuntimeLookupTable,
    RuntimeRetryPlan,
    RuntimeRetryResult,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunDetail,
    RuntimeRunLink,
    RuntimeRunQueryResult,
    RuntimeRunSnapshot,
    RuntimeRunType,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_detail_payload import (
    dataset_transaction_for_row,
    lineage_edges_for_row,
    quality_evidence_for_transaction,
    related_evidence_for_row,
    run_relations_for_row,
    runtime_detail_row_and_ai_payload,
    runtime_run_detail_payload,
    scoped_source_runs,
)
from foundry_lite.application.services.runtime_error_payloads import (
    dead_letter_retry_plan,
    link_dlq_retry,
    redact_sensitive,
    require_outbox_retry_open,
)
from foundry_lite.application.services.runtime_evidence_service import RuntimeEvidenceService
from foundry_lite.application.services.runtime_observability import RuntimeObservabilityMixin
from foundry_lite.application.services.runtime_run_paging import (
    OPERATIONS_RUN_DEFAULT_LIMIT,
    OPERATIONS_RUN_MAX_LIMIT,
    query_runtime_run_page,
)
from foundry_lite.application.services.runtime_run_queries import (
    downstream_impact_graph,
    object_late_data_badge,
    optional_run_type,
    relation_resource_ids,
    required_run_type,
    source_run_chain,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class RuntimeService(RuntimeObservabilityMixin, CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "runtime_repository",
        "ai_run_repository",
        "dataset_transaction_repository",
        "dataset_quality_repository",
    )
    required_collaborators = ()
    dataset_transaction_repository: DatasetTransactionRepository
    evidence_service: RuntimeEvidenceService

    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[LineageEdgeRow]:
        ctx = ctx or RequestContext()
        return self.runtime_repository.lineage_for_resource(tenant_id=ctx.tenant_id, resource_id=resource_id)

    def catalog_lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[LineageEdgeRow]:
        """Lineage at the granularity a catalog resource is browsed at, not per dataset version."""

        ctx = ctx or RequestContext()
        return self.runtime_repository.catalog_lineage_for_resource(tenant_id=ctx.tenant_id, resource_id=resource_id)

    def source_run_chain(
        self,
        source_dataset_version_id: str,
        *,
        object_type_api_name: str,
        ctx: RequestContext | None = None,
    ) -> list[RuntimeRunLink]:
        ctx = ctx or RequestContext()
        lineage_rows = self.runtime_repository.lineage_for_resource(
            tenant_id=ctx.tenant_id,
            resource_id=source_dataset_version_id,
        )
        snapshot = scoped_source_runs(
            self.runtime_repository,
            ctx,
            object_type_api_name,
            resource_ids=relation_resource_ids(source_dataset_version_id, lineage_rows),
            run_ids=[edge["created_by_run_id"] for edge in lineage_rows],
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
        snapshot = scoped_source_runs(self.runtime_repository, ctx, object_type_api_name)
        return object_late_data_badge(
            snapshot,
            source_dataset_version_id=source_dataset_version_id,
            object_type_api_name=object_type_api_name,
        )

    def list_runs(self, *, ctx: RequestContext | None = None) -> RuntimeRunSnapshot:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:summary")
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id, limit=OPERATIONS_RUN_MAX_LIMIT)
        return cast(RuntimeRunSnapshot, redact_sensitive(snapshot, self.policy.sensitive_column_names(ctx)))

    def list_release_audit_events(
        self,
        resource_refs: Sequence[tuple[str, str]],
        event_types: Sequence[str],
        *,
        limit: int = 100,
        ctx: RequestContext | None = None,
    ) -> list[RuntimeRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:detail")
        with self.engine.begin() as conn:
            rows = self.runtime_repository.audit_events_for_resources(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                resource_refs=resource_refs,
                event_types=event_types,
                limit=max(1, min(limit, OPERATIONS_RUN_MAX_LIMIT)),
            )
        return cast(list[RuntimeRow], redact_sensitive(rows, self.policy.sensitive_column_names(ctx)))

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
            actor_user_id=ctx.actor_user_id,
            tenant_id=ctx.tenant_id,
            run_type=parsed_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
        return cast(RuntimeRunQueryResult, redact_sensitive(page, self.policy.sensitive_column_names(ctx)))

    def run_detail(self, run_type: str, run_id: str, *, ctx: RequestContext | None = None) -> RuntimeRunDetail:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:detail")
        parsed_type = required_run_type(run_type)
        row, ai_payload = runtime_detail_row_and_ai_payload(self, self.ai_run_repository, ctx, parsed_type, run_id)
        outbox_events, audit_events, object_edits, action_writebacks = related_evidence_for_row(
            self.runtime_repository, tenant_id=ctx.tenant_id, run_row=row, limit=OPERATIONS_RUN_MAX_LIMIT
        )
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
            downstream_impact=self._downstream_impact(ctx, row, dataset_transaction),
            quality_check_results=quality_check_results,
            quality_failed_row_samples=quality_failed_row_samples,
        )
        if ai_payload is not None:
            detail["ai"] = ai_payload
        return cast(RuntimeRunDetail, redact_sensitive(detail, self.policy.sensitive_column_names(ctx)))

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
            outbox_event_id = self._retry_dead_letter_event_rows(conn, ctx, event_id, plan)
            event_type = plan["eventType"]
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
            link_dlq_retry(
                self._run_relation,
                conn,
                ctx,
                event_id=event_id,
                outbox_event_id=outbox_event_id,
                event_type=event_type,
            )
        return {**plan, "status": "pending"}

    def _retry_dead_letter_event_rows(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_id: str,
        plan: RuntimeRetryPlan,
    ) -> str:
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
        return outbox_event_id

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
        self.evidence_service._require_write_traffic_open(
            ctx, operation=operation, resource_type=resource_type, resource_id=resource_id
        )

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.evidence_service._require_or_audit(ctx, permission, resource_type, resource_id)

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
    ) -> RuntimeJsonObject | None:
        return downstream_impact_graph(
            row,
            dataset_transaction,
            lambda run_id: self.runtime_repository.run_row_any_type(tenant_id=ctx.tenant_id, run_id=run_id),
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
        self.evidence_service._audit(
            conn,
            ctx,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            decision=decision,
            policy_decision=policy_decision,
            before_ref=before_ref,
            after_ref=after_ref,
            correlation_id=correlation_id,
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
        return self.evidence_service._outbox(
            conn,
            ctx,
            event_type,
            aggregate_type,
            aggregate_id,
            payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def _outbox_event_by_idempotency_key(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        return self.evidence_service._outbox_event_by_idempotency_key(conn, ctx, idempotency_key)

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
        self.evidence_service._lineage(conn, ctx, from_type, from_id, to_type, to_id, relation, run_id)

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
        return self.evidence_service._run_relation(
            conn,
            ctx,
            source_run_type=source_run_type,
            source_run_id=source_run_id,
            target_run_type=target_run_type,
            target_run_id=target_run_id,
            relation=relation,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
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
        return self.evidence_service._error_payload(
            exc, ctx, run_id=run_id, correlation_id=correlation_id, adapter=adapter
        )
