"""Deterministic consumer Ontology MCP tool-schema builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.domain.platform.scopes import resource_scope


def business_system_tool() -> dict[str, object]:
    """Describe the application-owned work-screen contract without developer vocabulary."""

    return _tool(
        "business_system.get",
        "Open this application's shared work screens, roles, governed actions, and evidence rules.",
        {},
        [],
        is_write=False,
    )


def object_tools(name: str, scopes: tuple[str, ...]) -> list[dict[str, object]]:
    if resource_scope("object", name, "read") not in scopes:
        return []
    return [
        _object_get_tool(name),
        _object_unified_search_tool(name),
        _object_search_tool(name),
        _object_links_tool(name),
        _object_search_around_tool(name),
    ]


def _object_get_tool(name: str) -> dict[str, object]:
    return _tool(
        f"object.{name}.get",
        f"Get one permitted {name} object by ID.",
        {"objectId": {"type": "string", "pattern": r"\S"}},
        ["objectId"],
        is_write=False,
    )


def _object_unified_search_tool(name: str) -> dict[str, object]:
    return _tool(
        f"object.{name}.unifiedSearch",
        (
            f"Find {name} objects by their own fields AND by the content of documents bound to them. "
            f"Returns objects, not document chunks: a match inside an attached PDF, transcript, or "
            f"video frame lifts its owning object into the ranking, with the citation that caused it. "
            f"Use this when the answer may live in an attachment rather than in a property."
        ),
        {
            "query": {"type": "string", "pattern": r"\S"},
            "filter": {"type": "object"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        ["query"],
        is_write=False,
    )


def _object_links_tool(name: str) -> dict[str, object]:
    return _tool(
        f"object.{name}.links",
        (
            f"Follow one link type from a {name} object to the objects on the other side. "
            f"Search finds objects by what they contain; this finds them by what they are "
            f"connected to, which no filter on {name}'s own properties can express. The link "
            f"type needs its own read scope, so a traversal never reveals a relationship the "
            f"caller was not granted."
        ),
        {
            "objectId": {"type": "string", "pattern": r"\S"},
            "linkType": {"type": "string", "pattern": r"\S"},
        },
        ["objectId", "linkType"],
        is_write=False,
    )


def _object_search_around_tool(name: str) -> dict[str, object]:
    return _tool(
        f"object.{name}.searchAround",
        (
            f"Start from a filtered set of {name} objects, follow up to three link hops, and get "
            f"back the set of objects on the far side. The object type changes at every hop, so "
            f"this answers questions whose answer is a different type than the question — which "
            f"communities discuss a concern, which ingredients the posts about one problem name. "
            f"A single-object link lookup cannot do this because the input is a set, not a row. "
            f"Every link type in the chain needs its own read scope."
        ),
        {
            "filter": {"type": "object", "description": f"Filter narrowing the starting {name} set."},
            "linkTypes": {
                "type": "array",
                "items": {"type": "string", "pattern": r"\S"},
                "minItems": 1,
                "maxItems": 3,
                "description": "Ordered link types to follow; each hop must start at the current type.",
            },
        },
        ["linkTypes"],
        is_write=False,
    )


def _object_search_tool(name: str) -> dict[str, object]:
    return _tool(
        f"object.{name}.search",
        f"Search permitted {name} objects with bounded pagination.",
        {
            "search": {"type": "string", "description": "Keyword match over indexed text."},
            "semanticText": {
                "type": "string",
                "description": (
                    "Meaning-based match over the object type's vector property. Use this INSTEAD "
                    "of `search`, not alongside it: the object query planner runs one retrieval "
                    "strategy per call and rejects a request that sets both. Prefer it when the "
                    "caller phrased an intent rather than the words that appear in the data."
                ),
            },
            "filter": {"type": "object"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "cursor": {"type": "string"},
        },
        [],
        is_write=False,
    )


def action_tool(
    name: str,
    operation: str,
    description: str,
    target_schema: Mapping[str, object],
    parameter_schema: Mapping[str, object],
    *,
    is_write: bool,
) -> dict[str, object]:
    suffix = (
        "Execute autonomously only when low-risk; otherwise return an approval requirement."
        if is_write
        else "Return an immutable governed EditPlan without committing."
    )
    return _tool(
        f"action.{name}.{operation}",
        f"{description} {suffix}",
        {
            "objectType": dict(target_schema),
            "objectId": {"type": "string", "pattern": r"\S"},
            "expectedObjectVersion": {"type": "integer", "minimum": 1},
            "params": parameter_schema,
        },
        ["objectType", "objectId", "expectedObjectVersion", "params"],
        is_write=is_write,
    )


def function_tools(name: str, scopes: tuple[str, ...], definition: Mapping[str, object]) -> list[dict[str, object]]:
    if resource_scope("function", name, "execute") not in scopes:
        return []
    return [
        _tool(
            f"function.{name}.execute",
            _function_description(name, definition),
            {"inputs": _function_input_schema(definition)},
            ["inputs"],
            is_write=False,
        )
    ]


def _function_input_schema(definition: Mapping[str, object]) -> dict[str, object]:
    raw_inputs = definition.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, Sequence) and not isinstance(raw_inputs, str | bytes) else ()
    properties: dict[str, object] = {}
    required: list[str] = []
    for value in inputs:
        if not isinstance(value, Mapping) or not isinstance(value.get("apiName"), str):
            continue
        name = str(value["apiName"])
        properties[name] = _function_value_schema(value)
        if value.get("required") is True:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _function_value_schema(definition: Mapping[str, object]) -> dict[str, object]:
    value_type = str(definition.get("type"))
    builder = _FUNCTION_SCHEMA_BUILDERS.get(value_type)
    return builder(definition) if builder else dict(_FUNCTION_VALUE_SCHEMAS.get(value_type, {"type": "object"}))


def _function_collection_schema(definition: Mapping[str, object]) -> dict[str, object]:
    return {
        "type": "array",
        "items": _function_value_schema({**definition, "type": definition.get("itemType", "string")}),
        "uniqueItems": definition.get("type") == "objectSet",
    }


def _function_struct_schema(definition: Mapping[str, object]) -> dict[str, object]:
    fields = definition.get("fields")
    rows = fields if isinstance(fields, Sequence) and not isinstance(fields, str | bytes) else ()
    typed_rows = tuple(field for field in rows if isinstance(field, Mapping) and isinstance(field.get("apiName"), str))
    properties = {str(field["apiName"]): _function_value_schema(field) for field in typed_rows}
    required = [str(field["apiName"]) for field in typed_rows if field.get("required") is True]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_FUNCTION_VALUE_SCHEMAS: Mapping[str, Mapping[str, object]] = {
    "string": {"type": "string"},
    "boolean": {"type": "boolean"},
    "integer": {"type": "integer"},
    "long": {"type": "integer"},
    "float": {"type": "number"},
    "decimal": {"type": "number"},
    "date": {"type": "string", "format": "date"},
    "timestamp": {"type": "string", "format": "date-time"},
    "media_reference": {"type": "string", "format": "foundry-media-reference"},
    "media": {"type": "object", "format": "foundry-media-reference"},
    "attachment": {"type": "object", "format": "foundry-attachment-reference"},
    "object": {"type": "object", "format": "foundry-object-reference"},
    "interface": {"type": "object", "format": "foundry-interface-reference"},
    "ontology_edit_batch": {"type": "object"},
}
_FUNCTION_SCHEMA_BUILDERS = {
    "array": _function_collection_schema,
    "objectSet": _function_collection_schema,
    "struct": _function_struct_schema,
}


def _function_description(name: str, definition: Mapping[str, object]) -> str:
    display_name = definition.get("displayName")
    version = definition.get("version")
    label = display_name if isinstance(display_name, str) and display_name else name
    version_label = version if isinstance(version, str) and version else "v1"
    return f"Execute the permitted, version-pinned query function {label} ({version_label})."


def run_status_tool() -> dict[str, object]:
    return _tool(
        "action_run.get",
        "Get durable status and evidence for an Action run visible to this application.",
        {"runId": {"type": "string", "pattern": r"\S"}},
        ["runId"],
        is_write=False,
    )


def approval_status_tool() -> dict[str, object]:
    return _tool(
        "action_approval.get",
        "Get read-only human approval and execution status for an Action proposal created by this MCP principal.",
        {"reviewId": {"type": "string", "pattern": r"\S"}},
        ["reviewId"],
        is_write=False,
    )


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, object],
    required: Sequence[str],
    *,
    is_write: bool,
) -> dict[str, object]:
    return {
        "name": name,
        "title": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": dict(properties),
            "required": list(required),
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": not is_write,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }
