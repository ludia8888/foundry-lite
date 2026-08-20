"""Application service helpers for sets workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    ObjectReadRepository,
    ObjectSetDefinition,
    ObjectSetObjectTypeRow,
    ObjectSetPayload,
    ObjectSetQueryResult,
    ObjectSetRecord,
    ObjectSetRow,
    ObjectTypeRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.query_filters import FILTER_OPERATIONS, validate_filter_ast
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.row_policies import row_policy_scope, row_visible
from foundry_lite.application.services.object_store.set_members import (
    MAX_SEARCH_AROUND_HOPS,
    collect_dynamic_object_set_members,
    collect_static_object_set_members,
    resolve_search_around_object_ids,
    search_around_next_object_type,
)
from foundry_lite.application.services.object_store.set_protocols import (
    SetLinkScopeBoundary,
    SetObjectQuery,
    SetOntologyLookup,
    SetRuntimeBoundary,
)
from foundry_lite.application.services.object_store.set_semantics import (
    NormalizedObjectSetDefinition,
    ObjectSetMembers,
    can_read_object_set,
    object_set_access_scope,
    object_set_definition_from_inputs,
    object_set_expires_at,
    object_set_is_expired,
    object_set_lifecycle,
    object_set_storage_visibility,
)
from foundry_lite.application.services.write_traffic_gate import require_write_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

OBJECT_SET_TYPES = {"static", "dynamic", "search_around"}


class ObjectSetsService(CoreService):
    required_dependencies = ("engine", "policy", "object_set_repository", "object_read_repository")
    required_collaborators = (
        "object_query_service",
        "ontology_service",
        "osdk_application_service",
        "runtime_service",
    )
    object_query_service: SetObjectQuery
    object_read_repository: ObjectReadRepository
    ontology_service: SetOntologyLookup
    osdk_application_service: SetLinkScopeBoundary
    runtime_service: SetRuntimeBoundary

    def create_object_set(
        self,
        name: str,
        object_type_api_name: str,
        *,
        set_type: str,
        ctx: RequestContext | None = None,
        definition: Mapping[str, object] | None = None,
        object_ids: list[str] | None = None,
        filter_ast: Mapping[str, object] | None = None,
        visibility: str | None = None,
        access_scope: str | None = None,
        lifecycle: str | None = None,
        ttl_seconds: int | None = None,
    ) -> ObjectSetPayload:
        ctx = ctx or RequestContext()
        self._require_object_set_create(ctx, name)
        normalized = self._normalize_object_set_definition(
            name,
            set_type=set_type,
            definition=definition,
            object_ids=object_ids,
            filter_ast=filter_ast,
            visibility=visibility,
            access_scope=access_scope,
            lifecycle=lifecycle,
            ttl_seconds=ttl_seconds,
        )
        return self._persist_object_set(ctx, name, object_type_api_name, set_type, normalized)

    def _require_object_set_create(self, ctx: RequestContext, name: str) -> None:
        self.runtime_service._require_or_audit(ctx, "object:set:manage", "object_set", name)
        require_write_open(self.runtime_service, ctx, "create_object_set", "object_set", name)

    def _persist_object_set(
        self,
        ctx: RequestContext,
        name: str,
        object_type_api_name: str,
        set_type: str,
        normalized: NormalizedObjectSetDefinition,
    ) -> ObjectSetPayload:
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            self._validate_object_set_definition(conn, ctx, object_type, normalized)
            set_id = self._create_object_set_record(
                conn,
                ctx,
                name=name.strip(),
                object_type=object_type,
                set_type=set_type,
                definition=normalized["definition"],
                visibility=normalized["visibility"],
                expires_at=normalized["expires_at"],
            )
            self._emit_object_set_created_events(conn, ctx, set_id, name.strip(), object_type_api_name, set_type)
            return self._object_set_payload(conn, ctx, set_id, include_items=True)

    def _create_object_set_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        name: str,
        object_type: ObjectTypeRow,
        set_type: str,
        definition: ObjectSetDefinition,
        visibility: str,
        expires_at: str | None,
    ) -> str:
        set_id = _new_id("oset")
        now = _now()
        self.object_set_repository.create_object_set(
            transaction=conn,
            record=ObjectSetRecord(
                set_id=set_id,
                tenant_id=ctx.tenant_id,
                name=name,
                object_type_id=object_type["id"],
                set_type=set_type,
                definition=definition,
                visibility=visibility,
                owner_user_id=ctx.actor_user_id,
                expires_at=expires_at,
                created_at=now,
            ),
        )
        return set_id

    def _emit_object_set_created_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        set_id: str,
        name: str,
        object_type_api_name: str,
        set_type: str,
    ) -> None:
        event_ref = {"name": name, "objectType": object_type_api_name, "setType": set_type}
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="object_set.created",
            resource_type="object_set",
            resource_id=set_id,
            action="create",
            after_ref=event_ref,
        )
        self.runtime_service._outbox(
            conn,
            ctx,
            "object_set.created",
            "object_set",
            set_id,
            event_ref,
            idempotency_key=set_id,
            correlation_id=ctx.request_id,
        )

    def get_object_set(
        self,
        set_id: str,
        *,
        ctx: RequestContext | None = None,
        include_items: bool = True,
    ) -> ObjectSetPayload:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
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
    ) -> ObjectSetQueryResult:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        with self.engine.begin() as conn:
            rows = self._visible_object_set_rows(conn, ctx, object_type_api_name=object_type_api_name)
            return {"items": [self._object_set_payload_from_row(conn, ctx, row, include_items=False) for row in rows]}

    def cleanup_expired_object_sets(self, *, ctx: RequestContext | None = None) -> dict[str, int]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "object:set:manage", "object_set", "expired")
        require_write_open(self.runtime_service, ctx, "cleanup_expired_object_sets", "object_set", "expired")
        with self.engine.begin() as conn:
            rows = self.object_set_repository.object_sets(transaction=conn, tenant_id=ctx.tenant_id)
            expired_ids = [row["id"] for row in rows if object_set_is_expired(row)]
            if expired_ids:
                self.object_set_repository.delete_object_sets(
                    transaction=conn,
                    tenant_id=ctx.tenant_id,
                    set_ids=expired_ids,
                )
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="object_set.expired_deleted",
                    resource_type="object_set",
                    resource_id=None,
                    action="cleanup",
                    after_ref={"deleted": len(expired_ids), "set_ids": expired_ids},
                )
                self.runtime_service._outbox(
                    conn,
                    ctx,
                    "object_set.expired_deleted",
                    "object_set",
                    "expired",
                    {"deleted": len(expired_ids), "setIds": expired_ids},
                    idempotency_key=f"object_set.cleanup:{ctx.request_id}",
                    correlation_id=ctx.request_id,
                )
            return {"deleted": len(expired_ids)}

    def resolve_search_around(
        self,
        from_object_type_api_name: str,
        link_types: Sequence[str],
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        include_items: bool = True,
    ) -> dict[str, object]:
        """Resolve a traversal chain without persisting an ObjectSet row."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        hops = [{"link": link_type} for link_type in link_types]
        with self.engine.begin() as conn:
            link_type_rows = self._link_types_by_api_name(conn, ctx)
            result_type = self._search_around_result_type(ctx, from_object_type_api_name, hops, link_type_rows)
            row = _transient_search_around_row(from_object_type_api_name, filter_ast, hops)
            object_ids, items = self._search_around_object_set_members(conn, ctx, result_type, row, include_items)
        return _search_around_payload(
            result_type, from_object_type_api_name, link_types, object_ids, items, include_items
        )

    def _search_around_result_type(
        self,
        ctx: RequestContext,
        from_object_type_api_name: str,
        hops: Sequence[Mapping[str, object]],
        link_type_rows: Mapping[str, Mapping[str, object]],
    ) -> str:
        result_type = from_object_type_api_name
        for index, hop in enumerate(hops):
            link_api = hop.get("link")
            if not isinstance(link_api, str) or not link_api:
                raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
            link_type = link_type_rows.get(link_api)
            if link_type is None:
                raise ValidationFailed("search-around link type not found", details={"linkType": link_api})
            self._require_link_read_scope(ctx, link_api)
            result_type = search_around_next_object_type(link_type, result_type)
        return result_type

    def _normalize_object_set_definition(
        self,
        name: str,
        *,
        set_type: str,
        definition: Mapping[str, object] | None,
        object_ids: list[str] | None,
        filter_ast: Mapping[str, object] | None,
        visibility: str | None,
        access_scope: str | None,
        lifecycle: str | None,
        ttl_seconds: int | None,
    ) -> NormalizedObjectSetDefinition:
        if not name.strip():
            raise ValidationFailed("object set name is required")
        if set_type not in OBJECT_SET_TYPES:
            raise ValidationFailed("unsupported object set type", details={"set_type": set_type})
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValidationFailed("ttl_seconds must be positive", details={"ttl_seconds": ttl_seconds})
        storage_visibility = object_set_storage_visibility(
            visibility=visibility,
            access_scope=access_scope,
            lifecycle=lifecycle,
            ttl_seconds=ttl_seconds,
        )
        normalized_definition = object_set_definition_from_inputs(set_type, definition, object_ids, filter_ast)
        return {
            "definition": normalized_definition,
            "visibility": storage_visibility,
            "expires_at": object_set_expires_at(ttl_seconds),
        }

    def _validate_object_set_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        normalized: NormalizedObjectSetDefinition,
    ) -> None:
        definition = normalized["definition"]
        if "ids" in definition:
            self._validate_static_object_set_ids(conn, ctx, object_type, definition["ids"])
        elif "filter" in definition:
            self._validate_dynamic_object_set_filter(conn, ctx, object_type, definition["filter"])
        elif "searchAround" in definition:
            self._validate_search_around_definition(conn, ctx, object_type, definition["searchAround"])
        else:
            raise ValidationFailed(
                "object set definition must include ids, filter, or searchAround",
                details={"definition": definition},
            )

    def _validate_search_around_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        search_around: object,
    ) -> None:
        """A search-around set declares the type it lands on, and we hold it to that.

        Palantir's traversal changes the object type at every hop, so the declared type of the
        set is a claim about where the chain ends. Checking it here means a mistyped chain fails
        at create time instead of quietly serving objects of the wrong type later.
        """
        source_type, filter_ast, hops = self._search_around_parts(search_around)
        link_types = self._link_types_by_api_name(conn, ctx)
        result_type = source_type
        for index, hop in enumerate(hops):
            link_api = hop.get("link")
            if not isinstance(link_api, str) or not link_api:
                raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
            link_type = link_types.get(link_api)
            if link_type is None:
                raise ValidationFailed("search-around link type not found", details={"linkType": link_api})
            # Traversal reads the link type, so it needs the same per-link grant a direct link
            # read needs; otherwise a set would be a way around the link scope gate.
            self._require_link_read_scope(ctx, link_api)
            result_type = search_around_next_object_type(link_type, result_type)
        if result_type != object_type["api_name"]:
            raise ValidationFailed(
                "search-around result type does not match the declared object type",
                details={"declared": object_type["api_name"], "resolved": result_type},
            )
        source = self.ontology_service._active_object_type(conn, ctx, source_type)
        if filter_ast:
            self._validate_dynamic_object_set_filter(conn, ctx, source, filter_ast)

    def _search_around_parts(
        self, search_around: object
    ) -> tuple[str, Mapping[str, object], Sequence[Mapping[str, object]]]:
        if not isinstance(search_around, Mapping):
            raise ValidationFailed("searchAround must be an object")
        source = search_around.get("from")
        if not isinstance(source, Mapping):
            raise ValidationFailed("searchAround requires a from set")
        source_type = source.get("objectType")
        if not isinstance(source_type, str) or not source_type:
            raise ValidationFailed("searchAround from requires an objectType")
        filter_ast = source.get("filter") or {}
        if not isinstance(filter_ast, Mapping):
            raise ValidationFailed("searchAround from filter must be an object")
        hops = search_around.get("hops")
        if not isinstance(hops, Sequence) or isinstance(hops, str) or not hops:
            raise ValidationFailed("searchAround requires at least one hop")
        if len(hops) > MAX_SEARCH_AROUND_HOPS:
            raise ValidationFailed(
                "search-around chain is too long",
                details={"hops": len(hops), "maxHops": MAX_SEARCH_AROUND_HOPS},
            )
        parsed_hops = []
        for hop in hops:
            if not isinstance(hop, Mapping):
                raise ValidationFailed("searchAround hop must be an object")
            parsed_hops.append(hop)
        return source_type, filter_ast, parsed_hops

    def _link_types_by_api_name(self, conn: TransactionContext, ctx: RequestContext) -> dict[str, Mapping[str, object]]:
        active = self.ontology_service._active_ontology_version(conn, ctx)
        return {
            row["api_name"]: cast(Mapping[str, object], row)
            for row in self.ontology_service._link_types_for_version(conn, ctx, active["id"])
        }

    def _require_link_read_scope(self, ctx: RequestContext, link_type_api_name: str) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="link",
            resource_api_name=link_type_api_name,
            operation="read",
        )

    def _validate_static_object_set_ids(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        object_ids: object,
    ) -> None:
        if not isinstance(object_ids, list) or not all(isinstance(item, str) and item for item in object_ids):
            raise ValidationFailed("static object set ids must be non-empty strings")
        requested_ids = cast(list[str], object_ids)
        existing = self.object_set_repository.active_object_ids(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type["api_name"],
            object_ids=requested_ids,
        )
        missing = [object_id for object_id in requested_ids if object_id not in existing]
        if missing:
            raise ValidationFailed("static object set references missing objects", details={"objectIds": missing})

    def _validate_dynamic_object_set_filter(
        self, conn: TransactionContext, ctx: RequestContext, object_type: ObjectTypeRow, filter_ast: object
    ) -> None:
        if not isinstance(filter_ast, dict) or not filter_ast:
            raise ValidationFailed("dynamic object set filter is required")
        property_names = self.object_set_repository.property_names_for_object_type(
            transaction=conn,
            object_type_id=object_type["id"],
        )
        masked_property_names = self.policy.masked_property_names(ctx, object_type["api_name"])
        typed_filter = cast(Mapping[str, object], filter_ast)
        self._validate_filter_ast(typed_filter, property_names, masked_property_names)
        property_data_types = {
            row["api_name"]: row["data_type"]
            for row in self.ontology_service._properties_for_object_type(conn, object_type["id"])
        }
        validate_filter_ast(typed_filter, property_data_types=property_data_types)

    def _validate_filter_ast(
        self, filter_ast: Mapping[str, object], property_names: set[str], masked_property_names: set[str]
    ) -> None:
        if "and" in filter_ast:
            self._validate_filter_group(filter_ast["and"], property_names, masked_property_names)
            return
        if "or" in filter_ast:
            self._validate_filter_group(filter_ast["or"], property_names, masked_property_names)
            return
        prop = filter_ast.get("property")
        op = filter_ast.get("op")
        if prop not in property_names:
            raise ValidationFailed("object set filter references missing property", details={"property": prop})
        if prop in masked_property_names:
            raise ValidationFailed("object set filter references masked property", details={"property": prop})
        if op not in FILTER_OPERATIONS:
            raise ValidationFailed("unsupported filter operation", details={"op": op})
        if "value" not in filter_ast:
            raise ValidationFailed("object set filter value is required", details={"property": prop})

    def _validate_filter_group(self, items: object, property_names: set[str], masked_property_names: set[str]) -> None:
        if not isinstance(items, list) or not items:
            raise ValidationFailed("logical filter group must be a non-empty list")
        for item in items:
            if not isinstance(item, dict):
                raise ValidationFailed("logical filter item must be an object")
            self._validate_filter_ast(cast(Mapping[str, object], item), property_names, masked_property_names)

    def _object_set_payload(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        set_id: str,
        *,
        include_items: bool,
    ) -> ObjectSetPayload:
        row = self._visible_object_set_row(conn, ctx, set_id)
        if row is None:
            raise NotFound("object set not found", details={"object_set_id": set_id})
        return self._object_set_payload_from_row(conn, ctx, row, include_items=include_items)

    def _object_set_payload_from_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: ObjectSetRow,
        *,
        include_items: bool,
    ) -> ObjectSetPayload:
        object_type = self._object_type_by_id(conn, ctx, row["object_type_id"])
        object_ids, items = self._object_set_members(conn, ctx, object_type["api_name"], row, include_items)
        payload: ObjectSetPayload = {
            "id": row["id"],
            "name": row["name"],
            "objectType": object_type["api_name"],
            "setType": row["set_type"],
            "definition": row["definition"],
            "visibility": row["visibility"],
            "accessScope": object_set_access_scope(row["visibility"]),
            "lifecycle": object_set_lifecycle(row),
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
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        row: ObjectSetRow,
        include_items: bool,
    ) -> ObjectSetMembers:
        if row["set_type"] == "static":
            return self._static_object_set_members(conn, ctx, object_type_api_name, row, include_items)
        if row["set_type"] == "search_around":
            return self._search_around_object_set_members(conn, ctx, object_type_api_name, row, include_items)
        return self._dynamic_object_set_members(ctx, object_type_api_name, row, include_items)

    def _static_object_set_members(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        row: ObjectSetRow,
        include_items: bool,
    ) -> ObjectSetMembers:
        object_ids = list(cast(Sequence[str], row["definition"]["ids"]))
        # Restricted callers load records even for id-only reads: hidden ids never surface.
        object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
        scope = row_policy_scope(
            object_type,
            ctx.roles,
            self.ontology_service._properties_for_object_type(conn, object_type["id"]),
        )
        if scope.is_unrestricted and not include_items:
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
        return collect_static_object_set_members(
            self.object_query_service,
            object_type_api_name,
            ctx=ctx,
            object_ids=object_ids,
            records=records,
            scope=scope,
            include_items=include_items,
        )

    def _dynamic_object_set_members(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        row: ObjectSetRow,
        include_items: bool,
    ) -> ObjectSetMembers:
        return collect_dynamic_object_set_members(
            self.object_query_service,
            object_type_api_name,
            ctx=ctx,
            filter_ast=cast(Mapping[str, object], row["definition"]["filter"]),
            include_items=include_items,
        )

    def _search_around_object_set_members(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        row: ObjectSetRow,
        include_items: bool,
    ) -> ObjectSetMembers:
        source_type, filter_ast, hops = self._search_around_parts(row["definition"]["searchAround"])
        source_ids = self._search_around_source_ids(ctx, source_type, filter_ast)
        link_types = self._link_types_by_api_name(conn, ctx)
        self._require_search_around_link_scopes(ctx, hops)
        resolved_type, object_ids = resolve_search_around_object_ids(
            self.object_read_repository,
            transaction=conn,
            tenant_id=ctx.tenant_id,
            from_object_type_api_name=source_type,
            from_object_ids=source_ids,
            hops=hops,
            link_types_by_api=link_types,
        )
        if resolved_type != object_type_api_name:
            raise ValidationFailed(
                "search-around result type does not match the stored object type",
                details={"declared": object_type_api_name, "resolved": resolved_type},
            )
        return self._visible_search_around_members(conn, ctx, object_type_api_name, object_ids, include_items)

    def _search_around_source_ids(
        self,
        ctx: RequestContext,
        source_type: str,
        filter_ast: Mapping[str, object],
    ) -> list[str]:
        object_ids, _ = collect_dynamic_object_set_members(
            self.object_query_service,
            source_type,
            ctx=ctx,
            filter_ast=filter_ast,
            include_items=False,
        )
        return object_ids

    def _require_search_around_link_scopes(self, ctx: RequestContext, hops: Sequence[Mapping[str, object]]) -> None:
        for index, hop in enumerate(hops):
            link_api = hop.get("link")
            if not isinstance(link_api, str) or not link_api:
                raise ValidationFailed("search-around hop requires a link type", details={"hop": index})
            self._require_link_read_scope(ctx, link_api)

    def _visible_search_around_members(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_ids: list[str],
        include_items: bool,
    ) -> ObjectSetMembers:
        """Return only active, row-visible traversal targets; no dangling link leaks an id."""
        records = {
            record["object_id"]: record
            for record in self.object_set_repository.active_object_records_by_ids(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=object_type_api_name,
                object_ids=object_ids,
            )
        }
        object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
        scope = row_policy_scope(
            object_type,
            ctx.roles,
            self.ontology_service._properties_for_object_type(conn, object_type["id"]),
        )
        visible_ids = [oid for oid in object_ids if oid in records and row_visible(scope, records[oid]["properties"])]
        if not include_items:
            return visible_ids, []
        items = [
            self.object_query_service._object_query_item(ctx, object_type_api_name, records[oid]) for oid in visible_ids
        ]
        return visible_ids, items

    def _visible_object_set_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        set_id: str,
    ) -> ObjectSetRow | None:
        normalized = self.object_set_repository.object_set_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            set_id=set_id,
        )
        if normalized is None or object_set_is_expired(normalized) or not can_read_object_set(ctx, normalized):
            return None
        return normalized

    def _visible_object_set_rows(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        object_type_api_name: str | None,
    ) -> list[ObjectSetRow]:
        object_type_id = None
        if object_type_api_name is not None:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            object_type_id = object_type["id"]
        rows = self.object_set_repository.object_sets(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
        )
        return [row for row in rows if not object_set_is_expired(row) and can_read_object_set(ctx, row)]

    def _object_type_by_id(
        self, conn: TransactionContext, ctx: RequestContext, object_type_id: str
    ) -> ObjectSetObjectTypeRow:
        row = self.object_set_repository.object_type_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
        )
        if row is None:
            raise NotFound("object type not found", details={"object_type_id": object_type_id})
        return row


def _transient_search_around_row(
    from_object_type_api_name: str,
    filter_ast: Mapping[str, object] | None,
    hops: Sequence[Mapping[str, object]],
) -> ObjectSetRow:
    return cast(
        ObjectSetRow,
        {
            "definition": {
                "searchAround": {
                    "from": {"objectType": from_object_type_api_name, "filter": dict(filter_ast or {})},
                    "hops": list(hops),
                }
            }
        },
    )


def _search_around_payload(
    result_type: str,
    from_object_type_api_name: str,
    link_types: Sequence[str],
    object_ids: Sequence[str],
    items: Sequence[object],
    include_items: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "objectType": result_type,
        "fromObjectType": from_object_type_api_name,
        "linkTypes": list(link_types),
        "objectIds": list(object_ids),
        "count": len(object_ids),
    }
    if include_items:
        payload["items"] = list(items)
    return payload
