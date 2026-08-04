"""Compass, governance, OSDK React, Platform Q&A, and Pilot tools for AI FDE."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_pilot import FdePilotService
from foundry_lite.application.services.aip.fde_platform_docs import search_platform_docs
from foundry_lite.application.services.aip.fde_tool_result import (
    FdePlatformToolError,
    FdePlatformToolRequest,
    required_text,
    scope_value,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.application.services.resource_catalog_service import ResourceCatalogService
from foundry_lite.domain.context import RequestContext


class FdeApplicationToolService(CoreService):
    """Dispatch application-building and governance reads to native services."""

    required_dependencies = ()
    required_collaborators = ("fde_pilot_service", "osdk_application_service", "resource_catalog_service")
    fde_pilot_service: FdePilotService
    osdk_application_service: OsdkApplicationService
    resource_catalog_service: ResourceCatalogService

    def execute(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        tool_id = request.spec.tool_id
        if tool_id == "resource.search":
            return self._search_resources(ctx, required_text(request.arguments, "query"))
        if tool_id == "resource.inspect":
            return self.resource_catalog_service.get_resource(required_text(request.arguments, "rid"), ctx=ctx)
        if tool_id == "governance.project.inspect":
            return self._project(ctx, required_text(request.arguments, "projectId"))
        if tool_id.startswith("osdk.application."):
            return self._osdk(ctx, request)
        if tool_id == "platform.docs.search":
            return search_platform_docs(
                required_text(request.arguments, "query"), _bounded_limit(request.arguments.get("maxResults"))
            )
        if tool_id == "pilot.application.plan":
            return self.fde_pilot_service.plan(request.arguments)
        if tool_id == "pilot.application.generate":
            return self.fde_pilot_service.generate(
                ctx,
                _mapping(request.arguments.get("plan"), "plan"),
                required_text(request.arguments, "idempotencyKey"),
            )
        raise FdePlatformToolError("unknown_fde_tool", f"unsupported application tool {tool_id}")

    def _search_resources(self, ctx: RequestContext, query: str) -> dict[str, object]:
        payload = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        terms = tuple(term for term in query.lower().split() if term)
        items = [item for item in _mapping_items(payload.get("items")) if _matches(item, terms)][:50]
        return {"query": query, "items": items, "count": len(items), "nextCursor": None}

    def _project(self, ctx: RequestContext, project_id: str) -> dict[str, object]:
        return {
            **self.resource_catalog_service.get_project(project_id, ctx=ctx),
            **self.resource_catalog_service.list_folders(project_id, ctx=ctx),
            "resources": self.resource_catalog_service.list_resources(
                project_id=project_id, folder_id=None, include_trashed=False, ctx=ctx
            )["items"],
        }

    def _osdk(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        app_id = scope_value(request.scope_ref, "osdk-app:")
        if request.spec.tool_id == "osdk.application.inspect":
            return dict(self.osdk_application_service.get_application(app_id, ctx=ctx))
        if request.spec.tool_id == "osdk.application.update_resources":
            return dict(
                self.osdk_application_service.update_resources(
                    app_id,
                    ctx=ctx,
                    resources=_mapping_items(request.arguments.get("resources")),
                    idempotency_key=required_text(request.arguments, "idempotencyKey"),
                )
            )
        raise FdePlatformToolError("unknown_fde_tool", f"unsupported OSDK tool {request.spec.tool_id}")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _mapping_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    if not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    return [{str(name): field for name, field in item.items()} for item in value if isinstance(item, Mapping)]


def _matches(item: Mapping[str, object], terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    text = " ".join(str(item.get(field, "")) for field in ("rid", "displayName", "resourceType", "sourceRef"))
    lowered = text.lower()
    return all(term in lowered for term in terms)


def _bounded_limit(value: object) -> int:
    if value is None:
        return 10
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 25:
        raise FdePlatformToolError("schema_invalid", "maxResults must be between 1 and 25")
    return value
