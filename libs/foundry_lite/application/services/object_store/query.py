from __future__ import annotations

from typing import Any

from foundry_lite.application.query_filters import matches_filter
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    NotFound,
)


class ObjectQueryService(CoreService):
    required_dependencies = ("engine", "policy", "object_read_repository")

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        explain: bool = False,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:read", object_type_api_name, object_id)
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
            payload = {
                "objectType": object_type_api_name,
                "objectId": object_id,
                "objectVersion": record["object_version"],
                "properties": properties,
                "sourceDatasetVersionId": record["source_dataset_version_id"],
            }
            if explain:
                payload["explain"] = {
                    "baseProperties": record["base_properties"],
                    "editProperties": record["edit_properties"],
                    "lineage": self.runtime_service.lineage_for_resource(record["source_dataset_version_id"], ctx=ctx)
                    if record["source_dataset_version_id"]
                    else [],
                }
            return payload

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: dict[str, Any] | None = None,
        order_by: list[dict[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
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
        conn: Any,
        ctx: RequestContext,
        object_type_api_name: str,
    ) -> list[dict[str, Any]]:
        return self.object_read_repository.active_object_rows(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type_api_name,
        )

    def _apply_object_query_options(
        self,
        records: list[dict[str, Any]],
        *,
        cursor: str | None,
        filter_ast: dict[str, Any] | None,
        order_by: list[dict[str, str]] | None,
    ) -> list[dict[str, Any]]:
        filtered = self._apply_object_cursor(records, cursor)
        filtered = self._apply_object_filter(filtered, filter_ast)
        return self._apply_object_sort(filtered, order_by)

    def _apply_object_cursor(
        self,
        records: list[dict[str, Any]],
        cursor: str | None,
    ) -> list[dict[str, Any]]:
        if cursor is None:
            return records
        return [row for row in records if row["object_id"] > cursor]

    def _apply_object_filter(
        self,
        records: list[dict[str, Any]],
        filter_ast: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not filter_ast:
            return records
        return [row for row in records if self._matches_filter(row["properties"], filter_ast)]

    def _apply_object_sort(
        self,
        records: list[dict[str, Any]],
        order_by: list[dict[str, str]] | None,
    ) -> list[dict[str, Any]]:
        if not order_by:
            return records
        sorted_records = list(records)
        for order in reversed(order_by):
            prop = order["property"]
            reverse = order.get("direction", "asc") == "desc"
            sorted_records.sort(key=lambda item: item["properties"].get(prop), reverse=reverse)
        return sorted_records

    def _object_query_item(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "objectType": object_type_api_name,
            "objectId": row["object_id"],
            "objectVersion": row["object_version"],
            "properties": self.policy.mask_properties(ctx, object_type_api_name, dict(row["properties"])),
        }

    def _matches_filter(self, properties: dict[str, Any], filter_ast: dict[str, Any]) -> bool:
        return matches_filter(properties, filter_ast)
