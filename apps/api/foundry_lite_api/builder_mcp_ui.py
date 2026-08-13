"""MCP Apps resource and private confirmation tool for the Builder plane."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.domain.errors import NotFound, ValidationFailed

BUILDER_CONFIRMATION_TOOL = "approve_builder_mutation"
BUILDER_CONFIRMATION_RESOURCE_URI = "ui://foundry-lite/builder-confirmation-v1.html"
BUILDER_CONFIRMATION_MIME_TYPE = "text/html;profile=mcp-app"
DOMAIN_OS_RESOURCE_URI = "ui://foundry-lite/domain-os-studio-v1-354e3901f43f.html"
_BUILDER_CONFIRMATION_PATH = Path(__file__).resolve().parents[3] / "apps" / "chatgpt-builder-widget" / "index.html"
_DOMAIN_OS_ROOT = Path(__file__).resolve().parents[3] / "apps" / "chatgpt-domain-os-widget"
_DOMAIN_OS_PATH = _DOMAIN_OS_ROOT / "index.html"
_DOMAIN_OS_OSDK_PATH = _DOMAIN_OS_ROOT / "foundry-lite-mcp-osdk.js"
_DOMAIN_OS_TOOLS = frozenset({"pilot.application.plan", "pilot.application.generate"})
_OSDK_MARKER = "/*__FOUNDRY_LITE_MCP_OSDK__*/"


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


def builder_resource_descriptors() -> list[dict[str, object]]:
    """Advertise both generic mutation review and the task-first Domain OS Studio."""

    return [builder_resource_descriptor(), _domain_os_resource_descriptor()]


def read_builder_resource(params: Mapping[str, object]) -> dict[str, object]:
    uri = _required_text(params, "uri")
    if uri == DOMAIN_OS_RESOURCE_URI:
        return _read_domain_os_resource(uri)
    if uri != BUILDER_CONFIRMATION_RESOURCE_URI:
        raise NotFound("Builder MCP UI resource was not found", details={"uri": uri})
    text = _read_asset(_BUILDER_CONFIRMATION_PATH, uri, "apps/chatgpt-builder-widget/index.html")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": BUILDER_CONFIRMATION_MIME_TYPE,
                "text": text,
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
    if descriptor.get("name") in _DOMAIN_OS_TOOLS:
        descriptor["_meta"] = _domain_os_tool_meta(descriptor.get("_meta"))
        return descriptor
    annotations = descriptor.get("annotations")
    if isinstance(annotations, Mapping) and annotations.get("readOnlyHint") is False:
        descriptor["_meta"] = _mutation_tool_meta(descriptor.get("_meta"))
    return descriptor


def _domain_os_tool_meta(value: object) -> dict[str, object]:
    if value is not None and not isinstance(value, Mapping):
        raise ValidationFailed("Domain OS tool descriptor _meta must be an object")
    meta = {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}
    raw_ui = meta.get("ui", {})
    if not isinstance(raw_ui, Mapping):
        raise ValidationFailed("Domain OS tool descriptor _meta.ui must be an object")
    meta["ui"] = {**raw_ui, "resourceUri": DOMAIN_OS_RESOURCE_URI}
    meta["openai/outputTemplate"] = DOMAIN_OS_RESOURCE_URI
    meta["openai/widgetAccessible"] = True
    return meta


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


def _domain_os_resource_descriptor() -> dict[str, object]:
    return {
        "uri": DOMAIN_OS_RESOURCE_URI,
        "name": "foundry-lite-domain-os-studio",
        "title": "업무 OS 설계 검토",
        "description": "ChatGPT가 자연어 업무 설명에서 찾은 사람, 기록, 상태, 규칙, 버튼과 증거를 검토합니다.",
        "mimeType": BUILDER_CONFIRMATION_MIME_TYPE,
        "_meta": _domain_os_resource_meta(),
    }


def _domain_os_resource_meta() -> dict[str, object]:
    return {
        "ui": {"prefersBorder": True, "csp": {"connectDomains": [], "resourceDomains": []}},
        "openai/widgetDescription": (
            "A non-developer Domain OS review map rendered from a natural-language business description."
        ),
        "openai/widgetPrefersBorder": True,
    }


def _read_domain_os_resource(uri: str) -> dict[str, object]:
    template = _read_asset(_DOMAIN_OS_PATH, uri, "apps/chatgpt-domain-os-widget/index.html")
    runtime = _read_asset(_DOMAIN_OS_OSDK_PATH, uri, "apps/chatgpt-domain-os-widget/foundry-lite-mcp-osdk.js")
    if template.count(_OSDK_MARKER) != 1:
        raise ValidationFailed("Domain OS MCP App is missing its high-level OSDK injection point")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": BUILDER_CONFIRMATION_MIME_TYPE,
                "text": template.replace(_OSDK_MARKER, runtime),
                "_meta": _domain_os_resource_meta(),
            }
        ]
    }


def _read_asset(path: Path, uri: str, expected_asset: str) -> str:
    if not path.is_file():
        raise NotFound("Builder MCP UI asset is not installed", details={"uri": uri, "expectedAsset": expected_asset})
    return path.read_text(encoding="utf-8")


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"Builder MCP {key} is required")
    return item.strip()


__all__ = [
    "BUILDER_CONFIRMATION_TOOL",
    "DOMAIN_OS_RESOURCE_URI",
    "builder_resource_descriptor",
    "builder_resource_descriptors",
    "decorate_builder_tool_list",
    "read_builder_resource",
    "validate_resources_list_params",
    "validate_widget_approval_arguments",
]
