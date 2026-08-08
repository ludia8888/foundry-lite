"""Read-only use cases for canonical Action Contract discovery."""

from __future__ import annotations

from foundry_lite.application.action_types import ActionCatalogItem, ActionCatalogPage
from foundry_lite.application.ports import ActionTypeRow, TransactionContext
from foundry_lite.application.services.action_catalog_payloads import (
    action_catalog_item,
    decode_action_catalog_cursor,
    encode_action_catalog_cursor,
)
from foundry_lite.application.services.action_permission_guards import can_access_action_type
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.osdk_service_principal_authorization import (
    ServicePrincipalAccessSessionBoundary,
    require_service_principal_scope,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed


class ActionDefinitionService(CoreService):
    """Expose active Action contracts after principal and app-scope checks."""

    required_dependencies = ("engine", "policy")
    required_collaborators = (
        "ontology_lookup_service",
        "osdk_access_session_service",
        "osdk_application_service",
    )
    ontology_lookup_service: OntologyLookupService
    osdk_access_session_service: ServicePrincipalAccessSessionBoundary
    osdk_application_service: ActionOsdkScopeBoundary

    def list_actions(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionCatalogPage:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "ontology:read")
        bounded_limit = _bounded_limit(limit)
        with self.engine.begin() as conn:
            active = self.ontology_lookup_service._active_ontology_version(conn, request_context)
            rows = self._action_rows(conn, request_context, active["id"])
        after = decode_action_catalog_cursor(cursor, active["id"], request_context.application_id)
        visible = [row for row in rows if self._is_visible(request_context, row)]
        page = _page_after(visible, after)[: bounded_limit + 1]
        items = [action_catalog_item(row, active["id"], request_context) for row in page[:bounded_limit]]
        next_cursor = _next_cursor(page, bounded_limit, active["id"], request_context.application_id)
        return {"items": items, "nextCursor": next_cursor}

    def get_action(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "ontology:read")
        self.osdk_application_service.require_resource_scope(
            request_context,
            resource_type="action",
            resource_api_name=action_api_name,
            operation="validate",
        )
        with self.engine.begin() as conn:
            active = self.ontology_lookup_service._active_ontology_version(conn, request_context)
            row = self.ontology_lookup_service._active_action_type(conn, request_context, action_api_name)
        if not can_access_action_type(request_context, row, "view"):
            raise PermissionDenied("permission denied to view Action Type", details={"actionType": action_api_name})
        return action_catalog_item(row, active["id"], request_context)

    def get_external_mcp_action(self, action_api_name: str, *, ctx: RequestContext) -> ActionCatalogItem:
        """Describe one exact app-granted Action for a non-elevated machine caller."""

        require_service_principal_scope(
            ctx,
            self.osdk_access_session_service,
            self.osdk_application_service,
            resource_type="action",
            resource_api_name=action_api_name,
            operation="validate",
        )
        with self.engine.begin() as conn:
            active = self.ontology_lookup_service._active_ontology_version(conn, ctx)
            row = self.ontology_lookup_service._active_action_type(conn, ctx, action_api_name)
        return action_catalog_item(row, active["id"], ctx)

    def action_schema(self, action_api_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return dict(self.get_action(action_api_name, ctx=ctx)["parameterSchema"])

    def _action_rows(self, conn: TransactionContext, ctx: RequestContext, version_id: str) -> list[ActionTypeRow]:
        object_rows = self.ontology_lookup_service._object_types_for_version(conn, ctx, version_id)
        interface_rows = self.ontology_lookup_service._interface_types_for_version(conn, ctx, version_id)
        rows = {
            action["id"]: action
            for target in (*object_rows, *interface_rows)
            for action in self.ontology_lookup_service._actions_for_target(conn, target["id"])
            if action["enabled"]
        }
        return sorted(rows.values(), key=lambda row: row["api_name"])

    def _is_visible(self, ctx: RequestContext, row: ActionTypeRow) -> bool:
        try:
            self.osdk_application_service.require_resource_scope(
                ctx, resource_type="action", resource_api_name=row["api_name"], operation="validate"
            )
        except PermissionDenied:
            return False
        return can_access_action_type(ctx, row, "view")


def _bounded_limit(limit: int) -> int:
    if limit < 1 or limit > 200:
        raise ValidationFailed("action catalog limit must be between 1 and 200")
    return limit


def _page_after(rows: list[ActionTypeRow], after: str | None) -> list[ActionTypeRow]:
    return rows if after is None else [row for row in rows if row["api_name"] > after]


def _next_cursor(
    rows: list[ActionTypeRow], limit: int, ontology_version_id: str, application_id: str | None
) -> str | None:
    if len(rows) <= limit:
        return None
    return encode_action_catalog_cursor(ontology_version_id, application_id, rows[limit - 1]["api_name"])
