from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from foundry_lite.application.ports import ObjectSetRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.query_filters import FILTER_OPERATIONS
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

OBJECT_SET_TYPES = {"static", "dynamic"}
OBJECT_SET_VISIBILITIES = {"private", "public", "temporary", "permanent"}
PUBLIC_OBJECT_SET_VISIBILITIES = {"public", "permanent"}
PRIVATE_OBJECT_SET_VISIBILITIES = {"private", "temporary"}


class ObjectSetsService(CoreService):
    required_dependencies = ("engine", "policy", "object_set_repository")
    required_collaborators = (
        "object_query_service",
        "ontology_service",
        "runtime_service",
    )

    def create_object_set(
        self,
        name: str,
        object_type_api_name: str,
        *,
        set_type: str,
        ctx: RequestContext | None = None,
        definition: dict[str, Any] | None = None,
        object_ids: list[str] | None = None,
        filter_ast: dict[str, Any] | None = None,
        visibility: str = "private",
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:read", "object_set", name)
        normalized = self._normalize_object_set_definition(
            name,
            set_type=set_type,
            definition=definition,
            object_ids=object_ids,
            filter_ast=filter_ast,
            visibility=visibility,
            ttl_seconds=ttl_seconds,
        )
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            self._validate_object_set_definition(conn, ctx, object_type, normalized)
            set_id = _new_id("oset")
            now = _now()
            self.object_set_repository.create_object_set(
                transaction=conn,
                record=ObjectSetRecord(
                    set_id=set_id,
                    tenant_id=ctx.tenant_id,
                    name=name.strip(),
                    object_type_id=object_type["id"],
                    set_type=set_type,
                    definition=normalized["definition"],
                    visibility=visibility,
                    owner_user_id=ctx.actor_user_id,
                    expires_at=normalized["expires_at"],
                    created_at=now,
                ),
            )
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="object_set.created",
                resource_type="object_set",
                resource_id=set_id,
                action="create",
                after_ref={"name": name.strip(), "objectType": object_type_api_name, "setType": set_type},
            )
            self.runtime_service._outbox(
                conn,
                ctx,
                "object_set.created",
                "object_set",
                set_id,
                {"name": name.strip(), "objectType": object_type_api_name, "setType": set_type},
                idempotency_key=set_id,
                correlation_id=ctx.request_id,
            )
            return self._object_set_payload(conn, ctx, set_id, include_items=True)

    def get_object_set(
        self,
        set_id: str,
        *,
        ctx: RequestContext | None = None,
        include_items: bool = True,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:read", "object_set", set_id)
        with self.engine.begin() as conn:
            row = self._visible_object_set_row(conn, ctx, set_id)
            if row is None:
                raise NotFound("object set not found", details={"object_set_id": set_id})
            return self._object_set_payload_from_row(conn, ctx, row, include_items=include_items)

    def query_object_sets(
        self,
        *,
        ctx: RequestContext | None = None,
        object_type_api_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        with self.engine.begin() as conn:
            rows = self._visible_object_set_rows(conn, ctx, object_type_api_name=object_type_api_name)
            return {"items": [self._object_set_payload_from_row(conn, ctx, row, include_items=False) for row in rows]}

    def cleanup_expired_object_sets(self, *, ctx: RequestContext | None = None) -> dict[str, int]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        with self.engine.begin() as conn:
            rows = self.object_set_repository.object_sets(transaction=conn, tenant_id=ctx.tenant_id)
            expired_ids = [row["id"] for row in rows if self._object_set_is_expired(row)]
            self.object_set_repository.delete_object_sets(transaction=conn, set_ids=expired_ids)
            return {"deleted": len(expired_ids)}

    def _normalize_object_set_definition(
        self,
        name: str,
        *,
        set_type: str,
        definition: dict[str, Any] | None,
        object_ids: list[str] | None,
        filter_ast: dict[str, Any] | None,
        visibility: str,
        ttl_seconds: int | None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValidationFailed("object set name is required")
        if set_type not in OBJECT_SET_TYPES:
            raise ValidationFailed("unsupported object set type", details={"set_type": set_type})
        if visibility not in OBJECT_SET_VISIBILITIES:
            raise ValidationFailed("unsupported object set visibility", details={"visibility": visibility})
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValidationFailed("ttl_seconds must be positive", details={"ttl_seconds": ttl_seconds})
        normalized_definition = self._definition_from_inputs(set_type, definition, object_ids, filter_ast)
        return {
            "definition": normalized_definition,
            "expires_at": self._expires_at(ttl_seconds),
        }

    def _definition_from_inputs(
        self,
        set_type: str,
        definition: dict[str, Any] | None,
        object_ids: list[str] | None,
        filter_ast: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if definition is not None:
            normalized = dict(definition)
            if set_type == "static" and set(normalized) == {"ids"}:
                return normalized
            if set_type == "dynamic" and set(normalized) == {"filter"}:
                return normalized
            raise ValidationFailed(
                "object set definition does not match set type",
                details={"set_type": set_type, "definition": normalized},
            )
        if set_type == "static":
            ids = object_ids or []
            return {"ids": ids}
        return {"filter": filter_ast or {}}

    def _validate_object_set_definition(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type: dict[str, Any],
        normalized: dict[str, Any],
    ) -> None:
        definition = normalized["definition"]
        if "ids" in definition:
            self._validate_static_object_set_ids(conn, ctx, object_type, definition["ids"])
        elif "filter" in definition:
            self._validate_dynamic_object_set_filter(conn, object_type, definition["filter"])
        else:
            raise ValidationFailed(
                "object set definition must include ids or filter",
                details={"definition": definition},
            )

    def _validate_static_object_set_ids(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type: dict[str, Any],
        object_ids: Any,
    ) -> None:
        if not isinstance(object_ids, list) or not all(isinstance(item, str) and item for item in object_ids):
            raise ValidationFailed("static object set ids must be non-empty strings")
        existing = self.object_set_repository.active_object_ids(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type["api_name"],
            object_ids=object_ids,
        )
        missing = [object_id for object_id in object_ids if object_id not in existing]
        if missing:
            raise ValidationFailed("static object set references missing objects", details={"objectIds": missing})

    def _validate_dynamic_object_set_filter(
        self,
        conn: Any,
        object_type: dict[str, Any],
        filter_ast: Any,
    ) -> None:
        if not isinstance(filter_ast, dict) or not filter_ast:
            raise ValidationFailed("dynamic object set filter is required")
        property_names = self.object_set_repository.property_names_for_object_type(
            transaction=conn,
            object_type_id=object_type["id"],
        )
        self._validate_filter_ast(filter_ast, property_names)

    def _validate_filter_ast(self, filter_ast: dict[str, Any], property_names: set[str]) -> None:
        if "and" in filter_ast:
            self._validate_filter_group(filter_ast["and"], property_names)
            return
        if "or" in filter_ast:
            self._validate_filter_group(filter_ast["or"], property_names)
            return
        prop = filter_ast.get("property")
        op = filter_ast.get("op")
        if prop not in property_names:
            raise ValidationFailed("object set filter references missing property", details={"property": prop})
        if op not in FILTER_OPERATIONS:
            raise ValidationFailed("unsupported filter operation", details={"op": op})
        if "value" not in filter_ast:
            raise ValidationFailed("object set filter value is required", details={"property": prop})

    def _validate_filter_group(self, items: Any, property_names: set[str]) -> None:
        if not isinstance(items, list) or not items:
            raise ValidationFailed("logical filter group must be a non-empty list")
        for item in items:
            if not isinstance(item, dict):
                raise ValidationFailed("logical filter item must be an object")
            self._validate_filter_ast(item, property_names)

    def _object_set_payload(
        self,
        conn: Any,
        ctx: RequestContext,
        set_id: str,
        *,
        include_items: bool,
    ) -> dict[str, Any]:
        row = self._visible_object_set_row(conn, ctx, set_id)
        if row is None:
            raise NotFound("object set not found", details={"object_set_id": set_id})
        return self._object_set_payload_from_row(conn, ctx, row, include_items=include_items)

    def _object_set_payload_from_row(
        self,
        conn: Any,
        ctx: RequestContext,
        row: dict[str, Any],
        *,
        include_items: bool,
    ) -> dict[str, Any]:
        object_type = self._object_type_by_id(conn, ctx, row["object_type_id"])
        object_ids, items = self._object_set_members(conn, ctx, object_type["api_name"], row, include_items)
        payload = {
            "id": row["id"],
            "name": row["name"],
            "objectType": object_type["api_name"],
            "setType": row["set_type"],
            "definition": row["definition"],
            "visibility": row["visibility"],
            "ownerUserId": row["owner_user_id"],
            "expiresAt": row["expires_at"],
            "createdAt": row["created_at"],
            "objectIds": object_ids,
        }
        if include_items:
            payload["items"] = items
        return payload

    def _object_set_members(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type_api_name: str,
        row: dict[str, Any],
        include_items: bool,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if row["set_type"] == "static":
            return self._static_object_set_members(conn, ctx, object_type_api_name, row, include_items)
        return self._dynamic_object_set_members(ctx, object_type_api_name, row, include_items)

    def _static_object_set_members(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type_api_name: str,
        row: dict[str, Any],
        include_items: bool,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        object_ids = list(row["definition"]["ids"])
        if not include_items:
            return object_ids, []
        records = {
            record["object_id"]: record
            for record in self.object_set_repository.active_object_records_by_ids(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=object_type_api_name,
                object_ids=object_ids,
            )
        }
        items = [
            self.object_query_service._object_query_item(ctx, object_type_api_name, dict(records[object_id]))
            for object_id in object_ids
            if object_id in records
        ]
        return object_ids, items

    def _dynamic_object_set_members(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        row: dict[str, Any],
        include_items: bool,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        result = self.object_query_service.query_objects(
            object_type_api_name, ctx=ctx, filter_ast=row["definition"]["filter"], limit=10_000
        )
        object_ids = [item["objectId"] for item in result["items"]]
        return object_ids, result["items"] if include_items else []

    def _visible_object_set_row(
        self,
        conn: Any,
        ctx: RequestContext,
        set_id: str,
    ) -> dict[str, Any] | None:
        normalized = self.object_set_repository.object_set_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            set_id=set_id,
        )
        if (
            normalized is None
            or self._object_set_is_expired(normalized)
            or not self._can_read_object_set(ctx, normalized)
        ):
            return None
        return normalized

    def _visible_object_set_rows(
        self,
        conn: Any,
        ctx: RequestContext,
        *,
        object_type_api_name: str | None,
    ) -> list[dict[str, Any]]:
        object_type_id = None
        if object_type_api_name is not None:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            object_type_id = object_type["id"]
        rows = self.object_set_repository.object_sets(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
        )
        return [row for row in rows if not self._object_set_is_expired(row) and self._can_read_object_set(ctx, row)]

    def _can_read_object_set(self, ctx: RequestContext, row: dict[str, Any]) -> bool:
        if row["visibility"] in PUBLIC_OBJECT_SET_VISIBILITIES:
            return True
        if row["visibility"] in PRIVATE_OBJECT_SET_VISIBILITIES and row["owner_user_id"] == ctx.actor_user_id:
            return True
        return ctx.has_role("admin")

    def _object_set_is_expired(self, row: dict[str, Any]) -> bool:
        expires_at = row.get("expires_at")
        if not expires_at:
            return False
        return datetime.fromisoformat(expires_at) <= datetime.now().astimezone()

    def _expires_at(self, ttl_seconds: int | None) -> str | None:
        if ttl_seconds is None:
            return None
        return (datetime.now().astimezone() + timedelta(seconds=ttl_seconds)).isoformat()

    def _object_type_by_id(self, conn: Any, ctx: RequestContext, object_type_id: str) -> dict[str, Any]:
        row = self.object_set_repository.object_type_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
        )
        if row is None:
            raise NotFound("object type not found", details={"object_type_id": object_type_id})
        return row
