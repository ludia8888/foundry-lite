"""MCP Apps resource and private confirmation tool for the Builder plane."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.domain.errors import NotFound, ValidationFailed

BUILDER_CONFIRMATION_TOOL = "approve_builder_mutation"
BUILDER_CONFIRMATION_RESOURCE_URI = "ui://foundry-lite/builder-confirmation-v1.html"
BUILDER_CONFIRMATION_MIME_TYPE = "text/html;profile=mcp-app"
_BUILDER_CONFIRMATION_PATH = Path(__file__).resolve().parents[3] / "apps" / "chatgpt-builder-widget" / "index.html"


def decorate_builder_tool_list(result: Mapping[str, object]) -> dict[str, object]:
    tools = result.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, str | bytes):
        raise ValidationFailed("Builder MCP tool registry returned an invalid tools list")
    decorated = [_decorate_tool(tool) for tool in tools]
    return {**result, "tools": [*decorated, _approval_tool()]}


def builder_resource_descriptor() -> dict[str, object]:
    return {
        "uri": BUILDER_CONFIRMATION_RESOURCE_URI,
        "name": "foundry-lite-builder-mutation-confirmation",
        "title": "Builder Change Confirmation",
        "description": "Review the exact target, persistence impact, and redacted input before one Builder mutation.",
        "mimeType": BUILDER_CONFIRMATION_MIME_TYPE,
        "_meta": _resource_meta(),
    }


def read_builder_resource(params: Mapping[str, object]) -> dict[str, object]:
    uri = _required_text(params, "uri")
    if uri != BUILDER_CONFIRMATION_RESOURCE_URI:
        raise NotFound("Builder MCP UI resource was not found", details={"uri": uri})
    if not _BUILDER_CONFIRMATION_PATH.is_file():
        raise NotFound(
            "Builder MCP UI asset is not installed",
            details={
                "uri": uri,
                "expectedAsset": "apps/chatgpt-builder-widget/index.html",
            },
        )
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": BUILDER_CONFIRMATION_MIME_TYPE,
                "text": _BUILDER_CONFIRMATION_PATH.read_text(encoding="utf-8"),
                "_meta": _resource_meta(),
            }
        ]
    }


def validate_resources_list_params(params: Mapping[str, object]) -> None:
    cursor = params.get("cursor")
    if "cursor" in params:
        if not isinstance(cursor, str) or not cursor:
            raise ValidationFailed("MCP resources/list cursor must be a non-empty string")
        raise ValidationFailed("MCP resources/list pagination cursor is not supported")
    metadata = params.get("_meta")
    if "_meta" in params and not isinstance(metadata, Mapping):
        raise ValidationFailed("MCP resources/list _meta must be an object")


def validate_widget_approval_arguments(value: object) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("Builder MCP app approval arguments must be an object")
    if set(value) != {"challengeId", "widgetApprovalToken"}:
        raise ValidationFailed("Builder MCP app approval requires exact challenge and token fields")
    return _required_text(value, "challengeId"), _required_text(value, "widgetApprovalToken")


def _decorate_tool(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("Builder MCP tool registry returned an invalid tool descriptor")
    descriptor = {str(key): item for key, item in value.items()}
    annotations = descriptor.get("annotations")
    if isinstance(annotations, Mapping) and annotations.get("readOnlyHint") is False:
        descriptor["_meta"] = _mutation_tool_meta(descriptor.get("_meta"))
    return descriptor


def _mutation_tool_meta(value: object) -> dict[str, object]:
    if value is not None and not isinstance(value, Mapping):
        raise ValidationFailed("Builder MCP tool descriptor _meta must be an object")
    meta = {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}
    raw_ui = meta.get("ui", {})
    if not isinstance(raw_ui, Mapping):
        raise ValidationFailed("Builder MCP tool descriptor _meta.ui must be an object")
    meta["ui"] = {**raw_ui, "resourceUri": BUILDER_CONFIRMATION_RESOURCE_URI}
    meta["openai/outputTemplate"] = BUILDER_CONFIRMATION_RESOURCE_URI
    meta["openai/widgetAccessible"] = True
    return meta


def _approval_tool() -> dict[str, object]:
    return {
        "name": BUILDER_CONFIRMATION_TOOL,
        "title": "Confirm exact Builder mutation",
        "description": "App-only confirmation of the exact Builder challenge rendered to the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "challengeId": {"type": "string"},
                "widgetApprovalToken": {"type": "string"},
            },
            "required": ["challengeId", "widgetApprovalToken"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "_meta": {
            "ui": {"visibility": ["app"]},
            "openai/visibility": "private",
            "openai/widgetAccessible": True,
        },
    }


def _resource_meta() -> dict[str, object]:
    return {
        "ui": {
            "prefersBorder": True,
            "csp": {"connectDomains": [], "resourceDomains": []},
        },
        "openai/widgetDescription": (
            "A Builder confirmation card showing the exact target, persistence impact, and redacted input before retry."
        ),
        "openai/widgetPrefersBorder": True,
    }


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"Builder MCP {key} is required")
    return item.strip()


__all__ = [
    "BUILDER_CONFIRMATION_TOOL",
    "builder_resource_descriptor",
    "decorate_builder_tool_list",
    "read_builder_resource",
    "validate_resources_list_params",
    "validate_widget_approval_arguments",
]
