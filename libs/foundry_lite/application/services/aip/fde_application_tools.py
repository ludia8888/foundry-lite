"""Compass, governance, OSDK React, Platform Q&A, and Pilot tools for AI FDE."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.services.aip.fde_application_tool_projections import (
    FdeLineageReader,
    dataset_tool_result,
    lineage_graph,
    pilot_generation_tool_result,
)
from foundry_lite.application.services.aip.fde_object_tools import search_around_ontology_objects
from foundry_lite.application.services.aip.fde_pilot import FdePilotService
from foundry_lite.application.services.aip.fde_platform_docs import (
    list_platform_sdk_apis,
    load_official_tool_document,
    load_platform_document,
    ontology_sdk_context,
    ontology_sdk_examples,
    platform_documentation_summaries,
    platform_sdk_api_reference,
    search_platform_docs,
)
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


class FdeObjectQueryReader(Protocol):
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
    ) -> Mapping[str, object]: ...

    def aggregate_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        select: Sequence[Mapping[str, object]] | None = None,
    ) -> Mapping[str, object]: ...


class FdeObjectLinkReader(Protocol):
    def get_links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Sequence[Mapping[str, object]]: ...


class FdeObjectSetResolver(Protocol):
    def resolve_search_around(
        self,
        from_object_type_api_name: str,
        link_types: Sequence[str],
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        include_items: bool = True,
    ) -> Mapping[str, object]: ...


class FdeDatasetInspector(Protocol):
    def inspect_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        version: str = "latest",
    ) -> Mapping[str, object]: ...


class FdeApplicationToolService(CoreService):
    """Dispatch application-building and governance reads to native services."""

    required_dependencies = ()
    required_collaborators = (
        "dataset_registry_service",
        "fde_pilot_service",
        "object_links_service",
        "object_query_service",
        "object_sets_service",
        "osdk_application_service",
        "resource_catalog_service",
        "runtime_service",
    )
    dataset_registry_service: FdeDatasetInspector
    fde_pilot_service: FdePilotService
    object_links_service: FdeObjectLinkReader
    object_query_service: FdeObjectQueryReader
    object_sets_service: FdeObjectSetResolver
    osdk_application_service: OsdkApplicationService
    resource_catalog_service: ResourceCatalogService
    runtime_service: FdeLineageReader

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
            return {
                **self.fde_pilot_service.plan(request.arguments),
                "mcpExecution": {"mode": request.mode, "workspaceRef": request.scope_ref},
            }
        if tool_id == "pilot.application.generate":
            return pilot_generation_tool_result(
                self.fde_pilot_service.generate(
                    ctx,
                    _mapping(request.arguments.get("plan"), "plan"),
                    required_text(request.arguments, "idempotencyKey"),
                )
            )
        if tool_id in _PALANTIR_NATIVE_TOOL_IDS:
            return self._palantir_native(ctx, request)
        raise FdePlatformToolError("unknown_fde_tool", f"unsupported application tool {tool_id}")

    def _palantir_native(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        tool_id = request.spec.tool_id
        if tool_id in _PALANTIR_COMPASS_TOOL_IDS:
            return self._palantir_compass(ctx, request)
        if tool_id in _PALANTIR_OBJECT_TOOL_IDS:
            return self._palantir_objects(ctx, request)
        if tool_id in _PALANTIR_DATASET_TOOL_IDS:
            return self._palantir_dataset(ctx, request)
        if tool_id in _PALANTIR_LINEAGE_TOOL_IDS:
            return lineage_graph(
                self.runtime_service,
                ctx,
                required_text(request.arguments, "resourceId"),
                _bounded_graph_depth(request.arguments.get("maxDepth")),
            )
        if tool_id in _PALANTIR_ONTOLOGY_SDK_TOOL_IDS:
            return self._palantir_sdk_docs(request)
        if tool_id in _PALANTIR_PLATFORM_SDK_TOOL_IDS:
            return self._palantir_platform_sdk(request)
        if tool_id in _PALANTIR_DOC_TOOL_IDS:
            return self._palantir_docs(request)
        return self._palantir_osdk(ctx, request)

    def _palantir_compass(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        tool_id = request.spec.tool_id
        if tool_id == "list_resources_in_foundry_folder":
            return self.resource_catalog_service.list_resources(
                project_id=_optional_text(request.arguments, "projectId"),
                folder_id=required_text(request.arguments, "folderId"),
                include_trashed=False,
                ctx=ctx,
            )
        if tool_id == "get_project_imports":
            return self._project_imports(ctx, required_text(request.arguments, "projectId"))
        if tool_id == "create_foundry_project":
            return self.resource_catalog_service.create_project(
                display_name=required_text(request.arguments, "displayName"),
                description=_optional_text(request.arguments, "description"),
                metadata=_optional_mapping(request.arguments.get("metadata")),
                idempotency_key=required_text(request.arguments, "idempotencyKey"),
                ctx=ctx,
            )
        return self._search_projects(ctx, required_text(request.arguments, "query"))

    def _project_imports(self, ctx: RequestContext, project_id: str) -> dict[str, object]:
        payload = self.resource_catalog_service.list_resources(
            project_id=project_id,
            folder_id=None,
            include_trashed=False,
            ctx=ctx,
        )
        items = [item for item in _mapping_items(payload.get("items")) if item.get("resourceType") == "dataset"]
        return {"projectId": project_id, "items": items, "count": len(items), "nextCursor": None}

    def _palantir_objects(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        if request.spec.tool_id == "search_around_ontology_objects":
            return search_around_ontology_objects(self.object_sets_service, ctx, request)
        object_type = required_text(request.arguments, "objectType")
        if request.spec.tool_id == "traverse_ontology_object_links":
            link_type = required_text(request.arguments, "linkType")
            links = self.object_links_service.get_links(
                object_type,
                required_text(request.arguments, "objectId"),
                link_type,
                ctx=ctx,
            )
            items = [dict(link) for link in links]
            # Fan-out is capped by the link service, so a full page is a signal to the caller
            # that the traversal was cut rather than that the object has exactly this many.
            return {"objectType": object_type, "linkType": link_type, "items": items, "count": len(items)}
        if request.spec.tool_id == "query_ontology_objects":
            return dict(
                self.object_query_service.query_objects(
                    object_type,
                    ctx=ctx,
                    filter_ast=_optional_mapping_value(request.arguments.get("filter")),
                    order_by=_optional_order_by(request.arguments.get("orderBy")),
                    limit=_bounded_query_limit(request.arguments.get("limit")),
                    cursor=_optional_text(request.arguments, "cursor"),
                    search_text=_optional_text(request.arguments, "search"),
                )
            )
        return dict(
            self.object_query_service.aggregate_objects(
                object_type,
                ctx=ctx,
                filter_ast=_optional_mapping_value(request.arguments.get("filter")),
                group_by=_optional_string_items(request.arguments.get("groupBy")),
                select=_mapping_items(request.arguments.get("select")),
            )
        )

    def _palantir_dataset(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        dataset_ref = required_text(request.arguments, "datasetRef")
        if scope_value(request.scope_ref, "dataset:") != dataset_ref:
            raise FdePlatformToolError("scope_mismatch", "dataset tool must match the selected dataset scope")
        inspection = dict(
            self.dataset_registry_service.inspect_dataset(
                dataset_ref,
                ctx=ctx,
                version=_optional_text(request.arguments, "version") or "latest",
            )
        )
        return dataset_tool_result(request.spec.tool_id, inspection)

    def _palantir_docs(self, request: FdePlatformToolRequest) -> dict[str, object]:
        if request.spec.tool_id == "get_documentation_summaries":
            return platform_documentation_summaries()
        if request.spec.tool_id == "load_foundry_documentation_page":
            return load_platform_document(required_text(request.arguments, "documentId"))
        if request.spec.tool_id in _PALANTIR_EXACT_DOC_TOOL_IDS:
            return load_official_tool_document(
                request.spec.tool_id,
                _optional_text(request.arguments, "topic"),
            )
        return search_platform_docs(
            required_text(request.arguments, "query"), _bounded_limit(request.arguments.get("maxResults"))
        )

    def _palantir_sdk_docs(self, request: FdePlatformToolRequest) -> dict[str, object]:
        topic = _optional_text(request.arguments, "topic")
        if request.spec.tool_id == "get_ontology_sdk_context":
            return ontology_sdk_context(topic)
        return ontology_sdk_examples(topic, _optional_text(request.arguments, "language"))

    def _palantir_platform_sdk(self, request: FdePlatformToolRequest) -> dict[str, object]:
        if request.spec.tool_id == "list_platform_sdk_apis":
            return list_platform_sdk_apis(
                _optional_text(request.arguments, "product"),
                _bounded_platform_limit(request.arguments.get("maxResults")),
            )
        return platform_sdk_api_reference(required_text(request.arguments, "apiId"))

    def _palantir_osdk(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        app_id = scope_value(request.scope_ref, "osdk-app:")
        if request.spec.tool_id == "view_osdk_definition":
            return {
                "definition": dict(self.osdk_application_service.get_application(app_id, ctx=ctx)),
                "sdkVersions": self.osdk_application_service.list_sdk_versions(app_id, ctx=ctx),
                "install": self.osdk_application_service.install_metadata(app_id, ctx=ctx),
            }
        if request.spec.tool_id == "install_sdk_package":
            return dict(self.osdk_application_service.install_metadata(app_id, ctx=ctx))
        return dict(
            self.osdk_application_service.create_sdk_version(
                app_id,
                ctx=ctx,
                language=required_text(request.arguments, "language"),
                package_name=_optional_text(request.arguments, "packageName"),
                requested_bump=_optional_text(request.arguments, "requestedBump"),
                idempotency_key=required_text(request.arguments, "idempotencyKey"),
            )
        )

    def _search_resources(self, ctx: RequestContext, query: str) -> dict[str, object]:
        payload = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        terms = tuple(term for term in query.lower().split() if term)
        items = [item for item in _mapping_items(payload.get("items")) if _matches(item, terms)][:50]
        return {"query": query, "items": items, "count": len(items), "nextCursor": None}

    def _search_projects(self, ctx: RequestContext, query: str) -> dict[str, object]:
        projects = _mapping_items(self.resource_catalog_service.list_projects(ctx=ctx).get("projects"))
        terms = tuple(term for term in query.lower().split() if term)
        items = [item for item in projects if _matches_project(item, terms)][:50]
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


def _optional_mapping(value: object) -> dict[str, object]:
    return {} if value is None else _mapping(value, "metadata")


def _optional_mapping_value(value: object) -> Mapping[str, object] | None:
    return None if value is None else _mapping(value, "filter")


def _text_items(value: object, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list of non-empty strings")
    return [str(item) for item in value]


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


def _matches_project(item: Mapping[str, object], terms: tuple[str, ...]) -> bool:
    text = " ".join(str(item.get(field, "")) for field in ("id", "displayName", "description"))
    return all(term in text.lower() for term in terms)


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return item.strip() if isinstance(item, str) and item.strip() else None


def _optional_order_by(value: object) -> list[dict[str, str]] | None:
    if value is None:
        return None
    items = _mapping_items(value)
    return [{str(name): str(field) for name, field in item.items()} for item in items]


def _optional_string_items(value: object) -> list[str] | None:
    if value is None:
        return None
    if (
        not isinstance(value, Sequence)
        or isinstance(value, str | bytes)
        or not all(isinstance(item, str) for item in value)
    ):
        raise FdePlatformToolError("schema_invalid", "expected a list of strings")
    return [item for item in value if isinstance(item, str)]


def _bounded_query_limit(value: object) -> int:
    if value is None:
        return 20
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 50:
        raise FdePlatformToolError("schema_invalid", "limit must be between 1 and 50")
    return value


def _bounded_limit(value: object) -> int:
    if value is None:
        return 10
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 25:
        raise FdePlatformToolError("schema_invalid", "maxResults must be between 1 and 25")
    return value


def _bounded_platform_limit(value: object) -> int:
    if value is None:
        return 20
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 50:
        raise FdePlatformToolError("schema_invalid", "maxResults must be between 1 and 50")
    return value


def _bounded_graph_depth(value: object) -> int:
    if value is None:
        return 2
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 5:
        raise FdePlatformToolError("schema_invalid", "maxDepth must be between 1 and 5")
    return value


_PALANTIR_COMPASS_TOOL_IDS = frozenset(
    {
        "list_resources_in_foundry_folder",
        "get_project_imports",
        "create_foundry_project",
        "search_foundry_projects",
    }
)
_PALANTIR_OBJECT_TOOL_IDS = frozenset(
    {
        "query_ontology_objects",
        "aggregate_ontology_objects",
        "traverse_ontology_object_links",
        "search_around_ontology_objects",
    }
)
_PALANTIR_DATASET_TOOL_IDS = frozenset({"get_foundry_dataset_schema", "list_dataset_files", "get_dataset_stats"})
_PALANTIR_LINEAGE_TOOL_IDS = frozenset({"get_resource_graph"})
_PALANTIR_ONTOLOGY_SDK_TOOL_IDS = frozenset({"get_ontology_sdk_context", "get_ontology_sdk_examples"})
_PALANTIR_PLATFORM_SDK_TOOL_IDS = frozenset({"list_platform_sdk_apis", "get_platform_sdk_api_reference"})
_PALANTIR_EXACT_DOC_TOOL_IDS = frozenset(
    {
        "get_python_transforms_documentation",
        "get_typescript_v1_functions_documentation",
        "get_typescript_v2_functions_documentation",
        "get_custom_widget_documentation",
        "get_ml_documentation",
        "get_spark_profile_documentation",
        "get_osdk_react_components_documentation",
    }
)
_PALANTIR_DOC_TOOL_IDS = frozenset(
    {
        "get_documentation_summaries",
        "search_foundry_documentation",
        "load_foundry_documentation_page",
        *_PALANTIR_EXACT_DOC_TOOL_IDS,
    }
)
_PALANTIR_OSDK_TOOL_IDS = frozenset(
    {"view_osdk_definition", "generate_new_ontology_sdk_version", "install_sdk_package"}
)
_PALANTIR_NATIVE_TOOL_IDS = (
    _PALANTIR_COMPASS_TOOL_IDS
    | _PALANTIR_OBJECT_TOOL_IDS
    | _PALANTIR_DATASET_TOOL_IDS
    | _PALANTIR_LINEAGE_TOOL_IDS
    | _PALANTIR_ONTOLOGY_SDK_TOOL_IDS
    | _PALANTIR_PLATFORM_SDK_TOOL_IDS
    | _PALANTIR_DOC_TOOL_IDS
    | _PALANTIR_OSDK_TOOL_IDS
)
