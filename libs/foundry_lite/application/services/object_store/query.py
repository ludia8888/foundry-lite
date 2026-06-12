from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    LineageEdgeRow,
    ObjectPayload,
    ObjectQueryItem,
    ObjectQueryResult,
    ObjectRecordRow,
    TransactionContext,
)
from foundry_lite.application.ports.object_read_repository import ObjectExplain
from foundry_lite.application.query_filters import matches_filter
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.query_protocols import ObjectLineageReader, ObjectRecordLookup
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    NotFound,
)


class ObjectQueryService(CoreService):
    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = ("object_records_service", "runtime_service")
    object_records_service: ObjectRecordLookup
    runtime_service: ObjectLineageReader

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
            record = self.object_records_service._object_record(conn, ctx, object_type_api_name, object_id)
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
            payload: ObjectPayload = {
                "objectType": object_type_api_name,
                "objectId": object_id,
                "objectVersion": record["object_version"],
                "properties": properties,
                "sourceDatasetVersionId": record["source_dataset_version_id"],
            }
            if include_explain:
                payload["explain"] = self._object_explain(ctx, record)
            return payload

    def _object_explain(self, ctx: RequestContext, record: ObjectRecordRow) -> ObjectExplain:
        lineage_rows: list[LineageEdgeRow] = []
        source_run_chain = []
        if record["source_dataset_version_id"]:
            lineage_rows = self.runtime_service.lineage_for_resource(record["source_dataset_version_id"], ctx=ctx)
            source_run_chain = self.runtime_service.source_run_chain(
                record["source_dataset_version_id"],
                object_type_api_name=record["object_type_api_name"],
                ctx=ctx,
            )
        return {
            "baseProperties": record["base_properties"],
            "editProperties": record["edit_properties"],
            "lineage": _lineage_payload(lineage_rows),
            "sourceRunChain": source_run_chain,
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
    ) -> ObjectQueryResult:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        with self.engine.begin() as conn:
            records = self._object_query_rows(conn, ctx, object_type_api_name)
        records = self._apply_object_query_options(records, cursor=cursor, filter_ast=filter_ast, order_by=order_by)
        page = records[:limit]
        return {
            "items": [self._object_query_item(ctx, object_type_api_name, row) for row in page],
            "nextCursor": page[-1]["object_id"] if len(records) > len(page) and page else None,
        }

    def _object_query_rows(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
    ) -> list[ObjectRecordRow]:
        return self.object_read_repository.active_object_rows(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type_api_name,
        )

    def _apply_object_query_options(
        self,
        records: list[ObjectRecordRow],
        *,
        cursor: str | None,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[Mapping[str, str]] | None,
    ) -> list[ObjectRecordRow]:
        filtered = self._apply_object_cursor(records, cursor)
        filtered = self._apply_object_filter(filtered, filter_ast)
        return self._apply_object_sort(filtered, order_by)

    def _apply_object_cursor(
        self,
        records: list[ObjectRecordRow],
        cursor: str | None,
    ) -> list[ObjectRecordRow]:
        if cursor is None:
            return records
        return [row for row in records if row["object_id"] > cursor]

    def _apply_object_filter(
        self,
        records: list[ObjectRecordRow],
        filter_ast: Mapping[str, object] | None,
    ) -> list[ObjectRecordRow]:
        if not filter_ast:
            return records
        return [row for row in records if self._matches_filter(row["properties"], filter_ast)]

    def _apply_object_sort(
        self,
        records: list[ObjectRecordRow],
        order_by: Sequence[Mapping[str, str]] | None,
    ) -> list[ObjectRecordRow]:
        if not order_by:
            return records
        sorted_records = list(records)
        for order in reversed(order_by):
            prop = order["property"]
            reverse = order.get("direction", "asc") == "desc"
            sorted_records.sort(key=lambda item: _property_sort_key(item, prop), reverse=reverse)
        return sorted_records

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

    def _matches_filter(self, properties: Mapping[str, object], filter_ast: Mapping[str, object]) -> bool:
        return matches_filter(dict(properties), dict(filter_ast))


def _property_sort_key(row: ObjectRecordRow, property_name: str) -> tuple[int, float, str]:
    value = row["properties"].get(property_name)
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, 1.0 if value else 0.0, "")
    if isinstance(value, int | float):
        return (2, float(value), "")
    if isinstance(value, str):
        return (3, 0.0, value)
    return (4, 0.0, str(value))


def _lineage_payload(rows: Sequence[LineageEdgeRow]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]
