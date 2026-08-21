"""Application service helpers for sets workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    ObjectReadRepository,
    ObjectSetDefinition,
    ObjectSetPayload,
    ObjectSetQueryResult,
    ObjectSetRecord,
    ObjectSetRow,
    ObjectTypeRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.row_policies import row_policy_scope, row_visible
from foundry_lite.application.services.object_store.set_links import (
    link_types_by_api_name,
    require_link_read_scope,
)
from foundry_lite.application.services.object_store.set_members import (
    collect_dynamic_object_set_members,
    collect_static_object_set_members,
    resolve_search_around_object_ids,
)
from foundry_lite.application.services.object_store.set_protocols import (
    SetLinkScopeBoundary,
    SetObjectQuery,
    SetOntologyLookup,
    SetRuntimeBoundary,
)
from foundry_lite.application.services.object_store.set_rows import object_type_by_id
from foundry_lite.application.services.object_store.set_search_around import (
    require_search_around_link_reads,
    resolve_search_around_result_type,
    search_around_parts,
    search_around_payload,
    search_around_source_ids,
    transient_search_around_row,
)
from foundry_lite.application.services.object_store.set_semantics import (
    NormalizedObjectSetDefinition,
    ObjectSetMembers,
    can_read_object_set,
    object_set_access_scope,
    object_set_is_expired,
    object_set_lifecycle,
)
from foundry_lite.application.services.object_store.set_validation import (
    normalize_object_set_definition,
    validate_object_set_definition,
)
from foundry_lite.application.services.write_traffic_gate import require_write_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


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
        normalized = normalize_object_set_definition(
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
            validate_object_set_definition(
                conn,
                ctx,
                object_type,
                normalized,
                object_set_repository=self.object_set_repository,
                ontology_service=self.ontology_service,
                policy=self.policy,
                link_types_by_api=lambda connection, request_ctx: link_types_by_api_name(
                    self.ontology_service, connection, request_ctx
                ),
                require_link_read=lambda request_ctx, link_api: require_link_read_scope(
                    self.osdk_application_service, request_ctx, link_api
                ),
            )
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
            link_type_rows = link_types_by_api_name(self.ontology_service, conn, ctx)
            result_type = resolve_search_around_result_type(
                from_object_type_api_name,
                hops,
                link_type_rows,
                lambda link_api: require_link_read_scope(self.osdk_application_service, ctx, link_api),
            )
            row = transient_search_around_row(from_object_type_api_name, filter_ast, hops)
            object_ids, items = self._search_around_object_set_members(conn, ctx, result_type, row, include_items)
        return search_around_payload(
            result_type, from_object_type_api_name, link_types, object_ids, items, include_items
        )

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
        object_type = object_type_by_id(self.object_set_repository, conn, ctx, row["object_type_id"])
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
        source_type, filter_ast, hops = search_around_parts(row["definition"]["searchAround"])
        source_ids = search_around_source_ids(self.object_query_service, ctx, source_type, filter_ast)
        link_types = link_types_by_api_name(self.ontology_service, conn, ctx)
        require_search_around_link_reads(
            hops,
            lambda link_api: require_link_read_scope(self.osdk_application_service, ctx, link_api),
        )
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
