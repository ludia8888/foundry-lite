"""Read normalized Action Logs through the generic Ontology object contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.action_log_types import ACTION_LOG_PROPERTY_TYPES, ActionLogEntryRow
from foundry_lite.application.ports import (
    ActionRepository,
    ObjectAggregationResult,
    ObjectOrderBy,
    ObjectPayload,
    ObjectQueryCursor,
    ObjectQueryItem,
    ObjectQueryResult,
    OsdkResourceOperation,
    OsdkResourceType,
    TransactionContext,
)
from foundry_lite.application.ports.action_execution_repository import ActionExecutionRepository
from foundry_lite.application.services.action_log_ontology_query import (
    action_log_properties,
    action_log_query_item,
    decode_log_cursor,
    next_log_cursor,
    normalized_log_order,
    validate_log_filter,
    validate_log_group_by,
)
from foundry_lite.application.services.action_log_payloads import (
    action_api_name_from_log_object_type,
    action_log_payload,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.aggregation import (
    AGGREGATION_GROUP_LIMIT,
    build_aggregation_plan,
    finalize_aggregation_result,
)
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class _ActionScopeBoundary(Protocol):
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


class ActionLogOntologyService(CoreService):
    """Expose each Action's normalized log as one read-only virtual Object Type."""

    required_dependencies = ("engine", "policy", "action_repository", "action_execution_repository")
    required_collaborators = ("ontology_lookup_service", "osdk_application_service")
    action_repository: ActionRepository
    action_execution_repository: ActionExecutionRepository
    ontology_lookup_service: OntologyLookupService
    osdk_application_service: _ActionScopeBoundary

    @staticmethod
    def handles(object_type_api_name: str) -> bool:
        return action_api_name_from_log_object_type(object_type_api_name) is not None

    def get_object(
        self, object_type_api_name: str, object_id: str, *, ctx: RequestContext | None = None
    ) -> ObjectPayload:
        ctx, action_api_name = self._authorized_context(ctx, object_type_api_name)
        with self.engine.begin() as transaction:
            self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            row = self.action_repository.action_log_by_run_id(
                transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=object_id
            )
            if row is None or row["action_type_api_name"] != action_api_name:
                raise NotFound("Action Log object not found", details={"objectId": object_id})
            payload = self._payload(transaction, ctx, row)
        properties = action_log_properties(payload)
        return {
            "objectType": object_type_api_name,
            "objectId": object_id,
            "objectVersion": 2 if properties["revertedByRunId"] is not None else 1,
            "properties": properties,
            "sourceDatasetVersionId": None,
        }

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        search_text: str | None = None,
    ) -> ObjectQueryResult:
        ctx, action_api_name = self._authorized_context(ctx, object_type_api_name)
        bounded = _query_limit(limit)
        order = normalized_log_order(order_by)
        validate_log_filter(filter_ast)
        decoded = decode_log_cursor(
            cursor,
            ctx=ctx,
            object_type=object_type_api_name,
            order_by=order,
            filter_ast=filter_ast,
            search_text=search_text,
        )
        page, next_cursor = self._query_page(
            ctx, object_type_api_name, action_api_name, filter_ast, order, decoded, search_text, bounded
        )
        return {"items": page, "nextCursor": next_cursor}

    def aggregate_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        select: Sequence[Mapping[str, object]] | None = None,
    ) -> ObjectAggregationResult:
        ctx, action_api_name = self._authorized_context(ctx, object_type_api_name)
        validate_log_filter(filter_ast)
        validate_log_group_by(group_by)
        plan = build_aggregation_plan(
            object_type_api_name, ACTION_LOG_PROPERTY_TYPES, set(), group_by=group_by, select=select
        )
        with self.engine.begin() as transaction:
            self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            groups = self.action_repository.aggregate_action_logs(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                action_type_api_name=action_api_name,
                filter_ast=filter_ast,
                group_by=plan.group_by,
                metrics=plan.metrics,
                group_limit=AGGREGATION_GROUP_LIMIT + 1,
            )
        return finalize_aggregation_result(plan, groups)

    def _authorized_context(self, ctx: RequestContext | None, object_type_api_name: str) -> tuple[RequestContext, str]:
        resolved = ctx or RequestContext()
        action_api_name = action_api_name_from_log_object_type(object_type_api_name)
        if action_api_name is None:
            raise NotFound("Action Log object type not found", details={"objectType": object_type_api_name})
        self.policy.require(resolved, "object:read")
        self.policy.require(resolved, "action:log:read")
        self.osdk_application_service.require_resource_scope(
            resolved,
            resource_type="action",
            resource_api_name=action_api_name,
            operation="validate",
        )
        return resolved, action_api_name

    def _query_page(
        self,
        ctx: RequestContext,
        object_type: str,
        action_api_name: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
        cursor: ObjectQueryCursor | None,
        search_text: str | None,
        limit: int,
    ) -> tuple[list[ObjectQueryItem], str | None]:
        with self.engine.begin() as transaction:
            self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            self._require_cursor_unchanged(transaction, ctx, action_api_name, cursor, order_by)
            rows = self.action_repository.query_action_logs(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                action_type_api_name=action_api_name,
                filter_ast=filter_ast,
                order_by=order_by,
                cursor=cursor,
                search_text=search_text,
                limit=limit + 1,
            )
            items = [action_log_query_item(object_type, self._payload(transaction, ctx, row)) for row in rows[:limit]]
        token = next_log_cursor(
            items,
            has_more=len(rows) > limit,
            ctx=ctx,
            object_type=object_type,
            order_by=order_by,
            filter_ast=filter_ast,
            search_text=search_text,
        )
        return items, token

    def _require_cursor_unchanged(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_api_name: str,
        cursor: ObjectQueryCursor | None,
        order_by: Sequence[ObjectOrderBy],
    ) -> None:
        if cursor is None:
            return
        row = self.action_repository.action_log_by_run_id(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=cursor["object_id"]
        )
        if row is None or row["action_type_api_name"] != action_api_name:
            raise ValidationFailed("Action Log query cursor object was not found")
        properties = action_log_properties(self._payload(transaction, ctx, row))
        values = [properties.get(order["property"]) for order in order_by]
        if values != cursor["values"]:
            raise ValidationFailed("Action Log query cursor row changed")

    def _payload(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionLogEntryRow,
    ) -> dict[str, object]:
        objects = self.action_repository.action_log_objects(
            transaction=transaction, tenant_id=ctx.tenant_id, action_log_entry_id=str(row["id"])
        )
        run = self.action_repository.action_run_by_id(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=str(row["action_run_id"])
        )
        object_type = run["target_object_type_api_name"] if run else "unknown"
        parameters = self.policy.mask_sensitive_properties(ctx, object_type, dict(row["parameters"]))
        effects = self.action_execution_repository.effect_receipts_for_run(
            transaction=transaction, tenant_id=ctx.tenant_id, run_id=str(row["action_run_id"])
        )
        return action_log_payload(row, objects, parameters, len(effects))


def _query_limit(limit: int) -> int:
    if limit < 1 or limit > 500:
        raise ValidationFailed("Action Log object query limit must be between 1 and 500")
    return limit
