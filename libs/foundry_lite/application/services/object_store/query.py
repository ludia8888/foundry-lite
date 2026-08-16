"""Application service helpers for query workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import (
    LineageEdgeRow,
    ObjectAggregationGroup,
    ObjectAggregationResult,
    ObjectOrderBy,
    ObjectPayload,
    ObjectQueryItem,
    ObjectQueryResult,
    ObjectRecordRow,
    ObjectSortDirection,
    ObjectTypeRow,
    OsdkResourceOperation,
    OsdkResourceType,
    RuntimeJsonObject,
    RuntimeRunLink,
    TransactionContext,
)
from foundry_lite.application.ports.object_read_repository import ObjectExplain
from foundry_lite.application.query_filters import validate_filter_ast
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.aggregation import (
    ObjectAggregationPlan,
    aggregate_group_rows,
    build_aggregation_plan,
    finalize_aggregation_result,
    property_data_types,
)
from foundry_lite.application.services.object_store.evidence import object_property_lineage
from foundry_lite.application.services.object_store.query_cursor import (
    decode_object_query_cursor,
    encode_object_query_cursor,
)
from foundry_lite.application.services.object_store.query_protocols import (
    ObjectLineageReader,
    ObjectQueryOntologyLookup,
    ObjectRecordLookup,
    ObjectSearchQueryPlanner,
)
from foundry_lite.application.services.object_store.query_validation import (
    query_limit,
    require_visible_query_record,
    validate_query_properties,
)
from foundry_lite.application.services.object_store.row_policies import (
    row_policy_scope,
    scoped_filter_ast,
)
from foundry_lite.application.services.object_store.serving_contract import record_scope_object_type_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ValidationFailed,
)
from foundry_lite.domain.ontology.datasources import split_source_dataset_version_id


class _OsdkScopeBoundary(Protocol):
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


class ObjectQueryService(CoreService):
    required_dependencies = ("engine", "policy", "object_index_repository", "object_read_repository")
    required_collaborators = (
        "object_records_service",
        "runtime_service",
        "ontology_service",
        "object_search_service",
        "osdk_application_service",
    )
    object_records_service: ObjectRecordLookup
    runtime_service: ObjectLineageReader
    ontology_service: ObjectQueryOntologyLookup
    object_search_service: ObjectSearchQueryPlanner
    osdk_application_service: _OsdkScopeBoundary

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        self._require_object_read_scope(ctx, object_type_api_name)
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            record = self.object_records_service._object_record(
                conn,
                ctx,
                object_type_api_name,
                object_id,
                object_type_id=record_scope_object_type_id(object_type),
            )
            # Rows hidden by row policies 404 exactly like missing rows so a
            # restricted caller cannot probe for their existence.
            scope = row_policy_scope(
                object_type,
                ctx.roles,
                self.ontology_service._properties_for_object_type(conn, object_type["id"]),
            )
            record = require_visible_query_record(record, scope, object_type_api_name, object_id)
            properties = self.policy.mask_properties(
                ctx,
                object_type_api_name,
                dict(record["properties"]),
            )
            payload = self._build_object_payload(object_type_api_name, object_id, record, properties)
            # explain carries the base/edit property layers and operational
            # lineage/source metadata, so it is withheld unless the caller holds
            # object:explain — otherwise ?explain=true would bypass masking.
            if include_explain and self.policy.decide(ctx, "object:explain").allowed:
                payload["explain"] = self._object_explain(conn, ctx, record)
            return payload

    def _build_object_payload(
        self,
        object_type_api_name: str,
        object_id: str,
        record: ObjectRecordRow,
        properties: dict[str, object],
    ) -> ObjectPayload:
        """Build the object read payload, including deletion markers."""
        payload: ObjectPayload = {
            "objectType": object_type_api_name,
            "objectId": object_id,
            "objectVersion": record["object_version"],
            "properties": properties,
            "sourceDatasetVersionId": record["source_dataset_version_id"],
        }
        if record["deleted"]:
            payload["deleted"] = True
            payload["deletionReason"] = record["deletion_reason"]
        return payload

    def _source_explain_evidence(
        self,
        ctx: RequestContext,
        record: ObjectRecordRow,
        object_type_api_name: str,
    ) -> tuple[list[LineageEdgeRow], list[RuntimeRunLink], RuntimeJsonObject | None]:
        """Lineage/run-chain/badge for the record's source snapshot: a multi-datasource
        record stores a composite id, so lineage aggregates across every segment while
        the run chain and badge follow the primary segment."""
        if not record["source_dataset_version_id"]:
            return [], [], None
        segment_version_ids = split_source_dataset_version_id(record["source_dataset_version_id"])
        lineage_rows: list[LineageEdgeRow] = []
        for segment_version_id in segment_version_ids:
            lineage_rows.extend(self.runtime_service.lineage_for_resource(segment_version_id, ctx=ctx))
        source_run_chain = self.runtime_service.source_run_chain(
            segment_version_ids[0], object_type_api_name=object_type_api_name, ctx=ctx
        )
        late_data_badge = self.runtime_service.late_data_badge_for_source(
            segment_version_ids[0], object_type_api_name=object_type_api_name, ctx=ctx
        )
        return lineage_rows, source_run_chain, late_data_badge

    def _object_explain(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        record: ObjectRecordRow,
    ) -> ObjectExplain:
        object_type_api_name = record["object_type_api_name"]
        properties = self.ontology_service._properties_for_object_type(conn, record["object_type_id"])
        lineage_rows, source_run_chain, late_data_badge = self._source_explain_evidence(
            ctx, record, object_type_api_name
        )
        # The base/edit layers must honour the same masking as `properties`; a
        # caller who cannot see `properties.margin` must not read it here either.
        explain: ObjectExplain = {
            "baseProperties": self.policy.mask_properties(ctx, object_type_api_name, dict(record["base_properties"])),
            "editProperties": self.policy.mask_properties(ctx, object_type_api_name, dict(record["edit_properties"])),
            "lineage": _lineage_payload(lineage_rows),
            "propertyLineage": object_property_lineage(
                record,
                properties,
                masked_property_names=self.policy.masked_property_names(ctx, object_type_api_name),
            ),
            "sourceRunChain": source_run_chain,
        }
        if late_data_badge is not None:
            explain["lateDataBadge"] = late_data_badge
        return explain

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
        semantic_text: str | None = None,
    ) -> ObjectQueryResult:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        self._require_object_read_scope(ctx, object_type_api_name)
        query_limit_value = query_limit(limit)
        routed = self._search_route(
            object_type_api_name, ctx, search_text, semantic_text, query_limit_value, filter_ast, order_by, cursor
        )
        if routed is not None:
            return routed
        normalized_order_by = _normalized_order_by(order_by)
        if filter_ast:
            validate_filter_ast(filter_ast)
        records, active_index_version, effective_filter = self._query_active_records(
            ctx,
            object_type_api_name,
            filter_ast,
            normalized_order_by,
            cursor,
            query_limit_value,
        )
        page = records[:query_limit_value]
        return {
            "items": [self._object_query_item(ctx, object_type_api_name, row) for row in page],
            "nextCursor": _next_cursor(records, page, normalized_order_by, effective_filter, active_index_version, ctx),
        }

    def aggregate_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        select: Sequence[Mapping[str, object]] | None = None,
    ) -> ObjectAggregationResult:
        """Aggregate objects in the database so unmasked rows never leave the server.

        Runs the same gates as query_objects (role, osdk read scope, tenant,
        active index, masked-property checks) before any SQL executes.
        """
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        self._require_object_read_scope(ctx, object_type_api_name)
        if filter_ast:
            validate_filter_ast(filter_ast)
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            self.object_index_repository.active_index_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=object_type["id"],
            )
            self._validate_query_shape(conn, ctx, object_type_api_name, object_type["id"], filter_ast, [])
            plan = self._aggregation_plan(conn, ctx, object_type_api_name, object_type["id"], group_by, select)
            groups = self._aggregate_visible_groups(conn, ctx, object_type, filter_ast, plan)
        return finalize_aggregation_result(plan, groups)

    def _aggregate_visible_groups(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        filter_ast: Mapping[str, object] | None,
        plan: ObjectAggregationPlan,
    ) -> list[ObjectAggregationGroup]:
        """Row policies inject here so aggregates cannot reconstruct hidden rows."""
        scope = row_policy_scope(
            object_type,
            ctx.roles,
            self.ontology_service._properties_for_object_type(conn, object_type["id"]),
        )
        if scope.hides_all_rows:
            return []
        return aggregate_group_rows(
            self.object_read_repository,
            conn,
            tenant_id=ctx.tenant_id,
            object_type=object_type,
            filter_ast=scoped_filter_ast(scope, filter_ast),
            plan=plan,
        )

    def _aggregation_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_type_id: str,
        group_by: Sequence[str] | None,
        select: Sequence[Mapping[str, object]] | None,
    ) -> ObjectAggregationPlan:
        return build_aggregation_plan(
            object_type_api_name,
            property_data_types(self.ontology_service._properties_for_object_type(conn, object_type_id)),
            self.policy.masked_property_names(ctx, object_type_api_name),
            group_by=group_by,
            select=select,
        )

    def _require_object_read_scope(self, ctx: RequestContext, object_type_api_name: str) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="object",
            resource_api_name=object_type_api_name,
            operation="read",
        )

    def _search_route(
        self,
        object_type_api_name: str,
        ctx: RequestContext,
        search_text: str | None,
        semantic_text: str | None,
        query_limit: int,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[Mapping[str, str]] | None,
        cursor: str | None,
    ) -> ObjectQueryResult | None:
        """Route keyword/semantic searches to the planner; return None for the structured path."""
        if search_text and semantic_text:
            raise ValidationFailed("object query cannot combine keyword search with semantic search")
        if not search_text and not semantic_text:
            return None
        _validate_search_planner_shape(filter_ast, order_by, cursor)
        if semantic_text:
            return self.object_search_service.search_objects_semantic(
                object_type_api_name, ctx=ctx, semantic_text=semantic_text, limit=query_limit
            )
        return self.object_search_service.search_objects(
            object_type_api_name, ctx=ctx, search_text=str(search_text), limit=query_limit
        )

    def _query_active_records(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
        cursor: str | None,
        query_limit: int,
    ) -> tuple[list[ObjectRecordRow], str, Mapping[str, object] | None]:
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            active_index_version = self.object_index_repository.active_index_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=object_type["id"],
            )
            self._validate_query_shape(conn, ctx, object_type_api_name, object_type["id"], filter_ast, order_by)
            # Row policies are ANDed in AFTER shape validation: callers cannot
            # reference masked properties, but server-injected policy filters may.
            scope = row_policy_scope(
                object_type,
                ctx.roles,
                self.ontology_service._properties_for_object_type(conn, object_type["id"]),
            )
            if scope.hides_all_rows:
                return [], active_index_version, filter_ast
            effective_filter = scoped_filter_ast(scope, filter_ast)
            records = self._page_active_records(
                conn, ctx, object_type, effective_filter, order_by, cursor, query_limit, active_index_version
            )
        return records, active_index_version, effective_filter

    def _page_active_records(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
        cursor: str | None,
        query_limit: int,
        active_index_version: str,
    ) -> list[ObjectRecordRow]:
        cursor_state = decode_object_query_cursor(
            cursor,
            order_by,
            filter_ast,
            active_index_version,
            actor_user_id=ctx.actor_user_id,
            tenant_id=ctx.tenant_id,
        )
        return self.object_read_repository.query_active_object_rows(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type["api_name"],
            filter_ast=filter_ast,
            order_by=order_by,
            cursor=cursor_state,
            limit=query_limit + 1,
            object_type_id=record_scope_object_type_id(object_type),
            property_object_type_id=object_type["id"],
        )

    def _validate_query_shape(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_type_id: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
    ) -> None:
        properties = self.ontology_service._properties_for_object_type(conn, object_type_id)
        data_types = property_data_types(properties)
        masked = self.policy.masked_property_names(ctx, object_type_api_name)
        validate_query_properties(
            filter_ast,
            order_by,
            set(data_types),
            masked,
            property_data_types=data_types,
        )

    def _object_query_item(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        row: ObjectRecordRow,
    ) -> ObjectQueryItem:
        return {
            "objectType": object_type_api_name,
            "objectId": row["object_id"],
            "objectVersion": row["object_version"],
            "properties": self.policy.mask_properties(ctx, object_type_api_name, dict(row["properties"])),
        }


def _validate_search_planner_shape(
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[Mapping[str, str]] | None,
    cursor: str | None,
) -> None:
    if filter_ast or order_by or cursor:
        raise ValidationFailed("search query cannot be combined with filter, orderBy, or cursor")


def _normalized_order_by(order_by: Sequence[Mapping[str, str]] | None) -> list[ObjectOrderBy]:
    return [_normalized_order_item(item) for item in order_by or []]


def _normalized_order_item(item: Mapping[str, str]) -> ObjectOrderBy:
    prop = item.get("property")
    direction = item.get("direction", "asc")
    if not prop:
        raise ValidationFailed("object query orderBy property is required")
    return {"property": prop, "direction": _normalized_direction(direction)}


def _normalized_direction(direction: str) -> ObjectSortDirection:
    if direction == "asc":
        return "asc"
    if direction == "desc":
        return "desc"
    raise ValidationFailed("object query orderBy direction must be asc or desc", details={"direction": direction})


def _next_cursor(
    records: Sequence[ObjectRecordRow],
    page: Sequence[ObjectRecordRow],
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    active_index_version: str,
    ctx: RequestContext,
) -> str | None:
    if len(records) <= len(page) or not page:
        return None
    return encode_object_query_cursor(
        page[-1],
        order_by,
        filter_ast,
        active_index_version,
        actor_user_id=ctx.actor_user_id,
        tenant_id=ctx.tenant_id,
    )


def _lineage_payload(rows: Sequence[LineageEdgeRow]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]
