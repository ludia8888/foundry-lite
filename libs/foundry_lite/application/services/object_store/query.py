from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    LineageEdgeRow,
    ObjectOrderBy,
    ObjectPayload,
    ObjectQueryItem,
    ObjectQueryResult,
    ObjectRecordRow,
    ObjectSortDirection,
    TransactionContext,
)
from foundry_lite.application.ports.object_read_repository import ObjectExplain
from foundry_lite.application.query_filters import validate_filter_ast
from foundry_lite.application.services.base import CoreService
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
from foundry_lite.application.services.object_store.serving_contract import record_scope_object_type_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    NotFound,
    ValidationFailed,
)

OBJECT_QUERY_MAX_LIMIT = 500


class ObjectQueryService(CoreService):
    required_dependencies = ("engine", "policy", "object_index_repository", "object_read_repository")
    required_collaborators = ("object_records_service", "runtime_service", "ontology_service", "object_search_service")
    object_records_service: ObjectRecordLookup
    runtime_service: ObjectLineageReader
    ontology_service: ObjectQueryOntologyLookup
    object_search_service: ObjectSearchQueryPlanner

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
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            record = self.object_records_service._object_record(
                conn,
                ctx,
                object_type_api_name,
                object_id,
                object_type_id=record_scope_object_type_id(object_type),
            )
            if record is None:
                raise NotFound(
                    "object not found",
                    details={"object_type": object_type_api_name, "object_id": object_id},
                )
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

    def _object_explain(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        record: ObjectRecordRow,
    ) -> ObjectExplain:
        object_type_api_name = record["object_type_api_name"]
        lineage_rows: list[LineageEdgeRow] = []
        source_run_chain = []
        late_data_badge = None
        properties = self.ontology_service._properties_for_object_type(conn, record["object_type_id"])
        if record["source_dataset_version_id"]:
            lineage_rows = self.runtime_service.lineage_for_resource(record["source_dataset_version_id"], ctx=ctx)
            source_run_chain = self.runtime_service.source_run_chain(
                record["source_dataset_version_id"],
                object_type_api_name=object_type_api_name,
                ctx=ctx,
            )
            late_data_badge = self.runtime_service.late_data_badge_for_source(
                record["source_dataset_version_id"],
                object_type_api_name=object_type_api_name,
                ctx=ctx,
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
        query_limit = _query_limit(limit)
        routed = self._search_route(
            object_type_api_name, ctx, search_text, semantic_text, query_limit, filter_ast, order_by, cursor
        )
        if routed is not None:
            return routed
        normalized_order_by = _normalized_order_by(order_by)
        if filter_ast:
            validate_filter_ast(filter_ast)
        records, active_index_version = self._query_active_records(
            ctx,
            object_type_api_name,
            filter_ast,
            normalized_order_by,
            cursor,
            query_limit,
        )
        page = records[:query_limit]
        return {
            "items": [self._object_query_item(ctx, object_type_api_name, row) for row in page],
            "nextCursor": _next_cursor(records, page, normalized_order_by, filter_ast, active_index_version, ctx),
        }

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
    ) -> tuple[list[ObjectRecordRow], str]:
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            active_index_version = self.object_index_repository.active_index_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_id=object_type["id"],
            )
            self._validate_query_shape(conn, ctx, object_type_api_name, object_type["id"], filter_ast, order_by)
            cursor_state = decode_object_query_cursor(
                cursor,
                order_by,
                filter_ast,
                active_index_version,
                actor_user_id=ctx.actor_user_id,
                tenant_id=ctx.tenant_id,
            )
            records = self.object_read_repository.query_active_object_rows(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=object_type_api_name,
                filter_ast=filter_ast,
                order_by=order_by,
                cursor=cursor_state,
                limit=query_limit + 1,
                object_type_id=record_scope_object_type_id(object_type),
                property_object_type_id=object_type["id"],
            )
        return records, active_index_version

    def _validate_query_shape(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_type_id: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
    ) -> None:
        property_names = self._query_property_names(conn, object_type_id)
        masked = self.policy.masked_property_names(ctx, object_type_api_name)
        _validate_query_properties(filter_ast, order_by, property_names, masked)

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

    def _query_property_names(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> set[str]:
        return {
            property_type["api_name"]
            for property_type in self.ontology_service._properties_for_object_type(conn, object_type_id)
        }


def _query_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("object query limit must be positive", details={"limit": limit})
    if limit > OBJECT_QUERY_MAX_LIMIT:
        raise ValidationFailed(
            "object query limit exceeds maximum",
            details={"limit": limit, "max_limit": OBJECT_QUERY_MAX_LIMIT},
        )
    return limit


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


def _validate_query_properties(
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[ObjectOrderBy],
    property_names: set[str],
    masked_property_names: set[str],
) -> None:
    for order in order_by:
        _validate_query_property(order["property"], property_names, masked_property_names, source="orderBy")
    if filter_ast:
        _validate_filter_properties(filter_ast, property_names, masked_property_names)


def _validate_filter_properties(
    filter_ast: Mapping[str, object],
    property_names: set[str],
    masked_property_names: set[str],
) -> None:
    if "and" in filter_ast:
        for item in cast(Sequence[Mapping[str, object]], filter_ast["and"]):
            _validate_filter_properties(item, property_names, masked_property_names)
        return
    if "or" in filter_ast:
        for item in cast(Sequence[Mapping[str, object]], filter_ast["or"]):
            _validate_filter_properties(item, property_names, masked_property_names)
        return
    _validate_query_property(str(filter_ast["property"]), property_names, masked_property_names, source="filter")


def _validate_query_property(
    property_name: str,
    property_names: set[str],
    masked_property_names: set[str],
    *,
    source: str,
) -> None:
    _require_known_query_property(property_name, property_names, source=source)
    if property_name in masked_property_names:
        raise ValidationFailed(
            "object query references masked property",
            details={"property": property_name, "source": source},
        )


def _require_known_query_property(property_name: str, property_names: set[str], *, source: str) -> None:
    if property_name not in property_names:
        raise ValidationFailed(
            "object query references missing property",
            details={"property": property_name, "source": source},
        )


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
