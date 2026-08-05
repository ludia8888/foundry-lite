"""Official-name Palantir MCP tools backed by native Foundry-lite services."""

from __future__ import annotations

from foundry_lite.application.services.aip.tool_broker import ToolSpec


def _schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tool(
    tool_id: str,
    description: str,
    permission: str,
    properties: dict[str, object] | None = None,
    required: list[str] | None = None,
    effect: str = "READ",
    confirmation: str = "NONE",
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="v1",
        description=description,
        input_schema=_schema(properties or {}, required or []),
        output_schema={"type": "object"},
        effect=effect,  # type: ignore[arg-type]
        required_permission=permission,
        confirmation_policy=confirmation,  # type: ignore[arg-type]
        result_classification="internal",
    )


PALANTIR_MCP_NATIVE_TOOLS = (
    _tool(
        "list_resources_in_foundry_folder",
        "List permission-scoped resources in one Foundry-lite Compass folder.",
        "ontology:read",
        {"projectId": {"type": "string"}, "folderId": {"type": "string"}},
        ["folderId"],
    ),
    _tool(
        "get_project_imports",
        "List permission-scoped Dataset resources imported into one Foundry-lite project.",
        "ontology:read",
        {"projectId": {"type": "string"}},
        ["projectId"],
    ),
    _tool(
        "create_foundry_project",
        "Create one governed Foundry-lite project with idempotent audit and outbox evidence.",
        "developer_console:manage",
        {
            "displayName": {"type": "string"},
            "description": {"type": "string"},
            "metadata": {"type": "object"},
            "idempotencyKey": {"type": "string"},
        },
        ["displayName", "idempotencyKey"],
        "WRITE",
        "USER",
    ),
    _tool(
        "search_foundry_projects",
        "Search projects visible to the invoking user without revealing hidden projects.",
        "ontology:read",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "query_ontology_objects",
        "Query permission-scoped Ontology objects with server-side filters and bounded pagination.",
        "object:read",
        {
            "objectType": {"type": "string"},
            "filter": {"type": "object"},
            "orderBy": {"type": "array"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "cursor": {"type": "string"},
            "search": {"type": "string"},
        },
        ["objectType"],
    ),
    _tool(
        "aggregate_ontology_objects",
        "Aggregate permission-scoped Ontology objects without exporting raw hidden rows.",
        "object:read",
        {
            "objectType": {"type": "string"},
            "filter": {"type": "object"},
            "groupBy": {"type": "array", "items": {"type": "string"}},
            "select": {"type": "array"},
        },
        ["objectType", "select"],
    ),
    _tool(
        "get_foundry_dataset_schema",
        "Get the committed schema and version identity for one permission-scoped dataset.",
        "dataset:read",
        {"datasetRef": {"type": "string"}, "version": {"type": "string"}},
        ["datasetRef"],
    ),
    _tool(
        "list_dataset_files",
        "List only manifest-committed files for one permission-scoped dataset version.",
        "dataset:read",
        {"datasetRef": {"type": "string"}, "version": {"type": "string"}},
        ["datasetRef"],
    ),
    _tool(
        "get_dataset_stats",
        "Get manifest row, byte, partition, and column statistics for one committed dataset version.",
        "dataset:read",
        {"datasetRef": {"type": "string"}, "version": {"type": "string"}},
        ["datasetRef"],
    ),
    _tool(
        "get_resource_graph",
        "Return a bounded, permission-scoped graph from durable Foundry-lite lineage edges.",
        "operations:read:detail",
        {
            "resourceId": {"type": "string"},
            "maxDepth": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        ["resourceId"],
    ),
    _tool(
        "get_foundry_ontology_rid",
        "Get the selected branch and base Ontology version identities.",
        "ontology:validate",
    ),
    _tool(
        "search_foundry_ontology",
        "Search supported Ontology resources on the selected branch.",
        "ontology:validate",
        {"query": {"type": "string"}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 50}},
        ["query"],
    ),
    _tool(
        "search_foundry_functions",
        "Search function types on the selected Ontology branch.",
        "ontology:validate",
        {"query": {"type": "string"}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 50}},
        ["query"],
    ),
    *tuple(
        _tool(
            f"view_foundry_{resource_name}_type",
            f"View one {resource_name} type on the selected Ontology branch.",
            "ontology:validate",
            {"apiName": {"type": "string"}},
            ["apiName"],
        )
        for resource_name in ("object", "link", "action")
    ),
    _tool(
        "get_ontology_sdk_context",
        "Fetch curated Foundry-lite OSDK concepts and generated package guidance.",
        "ontology:read",
        {"topic": {"type": "string"}},
    ),
    _tool(
        "get_ontology_sdk_examples",
        "Fetch bounded Foundry-lite OSDK examples from the maintained SDK cookbook.",
        "ontology:read",
        {"topic": {"type": "string"}, "language": {"type": "string"}},
    ),
    _tool(
        "list_platform_sdk_apis",
        "List governed Foundry-lite Platform SDK surfaces from the generated API/SDK registry.",
        "ontology:read",
        {"product": {"type": "string"}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 50}},
    ),
    _tool(
        "get_platform_sdk_api_reference",
        "Get one exact Foundry-lite Platform SDK route, method, evidence, and proof contract.",
        "ontology:read",
        {"apiId": {"type": "string"}},
        ["apiId"],
    ),
    *tuple(
        _tool(
            f"create_or_update_foundry_{resource_name}_type",
            f"Create or update one {resource_name} type on the selected branch without activating production.",
            "ontology:validate",
            {"definition": {"type": "object"}, "changeSummary": {"type": "string"}},
            ["definition", "changeSummary"],
            "WRITE",
            "USER",
        )
        for resource_name in ("object", "link", "action")
    ),
    *tuple(
        _tool(
            f"delete_foundry_{resource_name}_type",
            f"Delete one {resource_name} type from the selected branch without changing the active Ontology.",
            "ontology:validate",
            {"apiName": {"type": "string"}, "changeSummary": {"type": "string"}},
            ["apiName", "changeSummary"],
            "WRITE",
            "USER",
        )
        for resource_name in ("object", "link", "action")
    ),
    _tool(
        "get_documentation_summaries",
        "List the curated Foundry-lite documentation catalog and current source paths.",
        "ontology:read",
    ),
    _tool(
        "search_foundry_documentation",
        "Search the curated Foundry-lite documentation catalog.",
        "ontology:read",
        {"query": {"type": "string"}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 25}},
        ["query"],
    ),
    _tool(
        "load_foundry_documentation_page",
        "Load a bounded page from the curated Foundry-lite documentation allowlist.",
        "ontology:read",
        {"documentId": {"type": "string"}},
        ["documentId"],
    ),
    *tuple(
        _tool(tool_id, description, "ontology:read", {"topic": {"type": "string"}})
        for tool_id, description in (
            ("get_python_transforms_documentation", "Fetch bounded Foundry-lite transform documentation."),
            ("get_typescript_v1_functions_documentation", "Fetch compatible function-contract documentation."),
            ("get_typescript_v2_functions_documentation", "Fetch canonical v2 function-contract documentation."),
            ("get_custom_widget_documentation", "Fetch Foundry-lite application and widget SDK guidance."),
            ("get_ml_documentation", "Fetch Foundry-lite model catalog and governed ML runtime guidance."),
            ("get_spark_profile_documentation", "Fetch Foundry-lite Spark compute profile guidance."),
            ("get_osdk_react_components_documentation", "Fetch Foundry-lite OSDK React component guidance."),
        )
    ),
    _tool(
        "view_osdk_definition",
        "View the selected Developer Console application, resource restrictions, and generated SDK state.",
        "developer_console:read",
    ),
    _tool(
        "generate_new_ontology_sdk_version",
        "Generate a new typed SDK artifact from the selected application's governed resources.",
        "developer_console:manage",
        {
            "language": {"type": "string", "enum": ["typescript", "python"]},
            "packageName": {"type": "string"},
            "requestedBump": {"type": "string", "enum": ["patch", "minor", "major"]},
            "idempotencyKey": {"type": "string"},
        },
        ["language", "idempotencyKey"],
        "WRITE",
        "USER",
    ),
    _tool(
        "install_sdk_package",
        "Return current generated SDK versions, release channels, and compatibility installation metadata.",
        "developer_console:read",
    ),
    _tool(
        "create_foundry_rest_api_data_source",
        "Create a governed REST Source and registered resource using secret references and network policy controls.",
        "connector:write",
        {
            "sourceName": {"type": "string"},
            "displayName": {"type": "string"},
            "baseUrl": {"type": "string"},
            "auth": {"type": "object"},
            "resourceName": {"type": "string"},
            "resourcePath": {"type": "string"},
            "datasetRef": {"type": "string"},
            "primaryKey": {"type": "array", "items": {"type": "string"}},
            "idempotencyKey": {"type": "string"},
        },
        [
            "sourceName",
            "displayName",
            "baseUrl",
            "auth",
            "resourceName",
            "resourcePath",
            "datasetRef",
            "idempotencyKey",
        ],
        "WRITE",
        "USER",
    ),
    _tool(
        "create_foundry_rest_api_data_source_webhook",
        "Create a signed webhook listener Source that appends into one governed dataset.",
        "source:write",
        {
            "sourceName": {"type": "string"},
            "displayName": {"type": "string"},
            "datasetRef": {"type": "string"},
            "connectorName": {"type": "string"},
            "resourceName": {"type": "string"},
            "signingSecretRef": {"type": "string"},
            "inboundUrl": {"type": "string"},
            "idempotencyKey": {"type": "string"},
        },
        [
            "sourceName",
            "displayName",
            "datasetRef",
            "connectorName",
            "resourceName",
            "signingSecretRef",
            "inboundUrl",
            "idempotencyKey",
        ],
        "WRITE",
        "USER",
    ),
    _tool(
        "view_foundry_rest_api_data_source_webhook",
        "View the durable configuration and current version fingerprint of one webhook listener Source.",
        "source:read",
        {"sourceName": {"type": "string"}},
        ["sourceName"],
    ),
    _tool(
        "get_or_create_network_egress_policy",
        "Get or idempotently create an allowlisted direct or agent-proxy network egress policy.",
        "source:write",
        {
            "policyName": {"type": "string"},
            "displayName": {"type": "string"},
            "mode": {"type": "string", "enum": ["direct", "agent_proxy"]},
            "allowedHosts": {"type": "array", "items": {"type": "string"}},
            "agentId": {"type": "string"},
            "idempotencyKey": {"type": "string"},
        },
        ["policyName", "displayName", "mode", "allowedHosts", "idempotencyKey"],
        "WRITE",
        "USER",
    ),
)


PALANTIR_MCP_TOOLS_BY_CAPABILITY = {
    "resource.search": ("resource.search", "search_foundry_projects"),
    "resource.inspect": ("resource.inspect", "list_resources_in_foundry_folder", "get_project_imports"),
    "governance.project.create": ("create_foundry_project",),
    "object.query": ("query_ontology_objects", "aggregate_ontology_objects"),
    "dataset.inspect": ("get_foundry_dataset_schema", "list_dataset_files", "get_dataset_stats"),
    "lineage.inspect": ("get_resource_graph",),
    "ontology.inspect": (
        "ontology.branch.inspect",
        "get_foundry_ontology_rid",
        "search_foundry_ontology",
        "search_foundry_functions",
        "view_foundry_object_type",
        "view_foundry_link_type",
        "view_foundry_action_type",
    ),
    "ontology.edit": (
        "ontology.branch.apply_patch",
        "create_or_update_foundry_object_type",
        "create_or_update_foundry_link_type",
        "create_or_update_foundry_action_type",
        "delete_foundry_object_type",
        "delete_foundry_link_type",
        "delete_foundry_action_type",
    ),
    "osdk.inspect": ("osdk.application.inspect", "view_osdk_definition", "install_sdk_package"),
    "osdk.docs": ("get_ontology_sdk_context", "get_ontology_sdk_examples"),
    "osdk.edit": ("osdk.application.update_resources", "generate_new_ontology_sdk_version"),
    "source.author": (
        "create_foundry_rest_api_data_source",
        "create_foundry_rest_api_data_source_webhook",
        "view_foundry_rest_api_data_source_webhook",
        "get_or_create_network_egress_policy",
    ),
    "platform.sdk.inspect": ("list_platform_sdk_apis", "get_platform_sdk_api_reference"),
    "platform.docs.search": (
        "platform.docs.search",
        "get_documentation_summaries",
        "search_foundry_documentation",
        "load_foundry_documentation_page",
        "get_python_transforms_documentation",
        "get_typescript_v1_functions_documentation",
        "get_typescript_v2_functions_documentation",
        "get_custom_widget_documentation",
        "get_ml_documentation",
        "get_spark_profile_documentation",
        "get_osdk_react_components_documentation",
    ),
}


PALANTIR_MCP_ONTOLOGY_TOOL_IDS = frozenset(
    tool_id
    for capability in ("ontology.inspect", "ontology.edit")
    for tool_id in PALANTIR_MCP_TOOLS_BY_CAPABILITY[capability]
    if not tool_id.startswith("ontology.")
)

PALANTIR_MCP_DATA_CONNECTION_TOOL_IDS = frozenset(
    {
        "create_foundry_rest_api_data_source",
        "create_foundry_rest_api_data_source_webhook",
        "view_foundry_rest_api_data_source_webhook",
        "get_or_create_network_egress_policy",
    }
)
