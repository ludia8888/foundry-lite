"""Application-owned work-screen resource for the consumer Ontology MCP plane."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.domain.errors import NotFound, ValidationFailed

BUSINESS_SYSTEM_RESOURCE_URI = "ui://foundry-lite/business-system-v3-231ce0f5d24c.html"
BUSINESS_SYSTEM_MIME_TYPE = "text/html;profile=mcp-app"
_ROOT = Path(__file__).resolve().parents[3] / "apps" / "chatgpt-business-system-widget"
_HTML_PATH = _ROOT / "index.html"
_OSDK_PATH = _ROOT / "foundry-lite-mcp-osdk.js"
_OSDK_MARKER = "/*__FOUNDRY_LITE_BUSINESS_SYSTEM_OSDK__*/"


def decorate_ontology_tools(result: Mapping[str, object]) -> dict[str, object]:
    tools = result.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, str | bytes):
        raise ValidationFailed("Ontology MCP tool registry returned an invalid tools list")
    return {**result, "tools": [_decorate_tool(tool) for tool in tools]}


def business_system_resource_descriptors() -> list[dict[str, object]]:
    return [
        {
            "uri": BUSINESS_SYSTEM_RESOURCE_URI,
            "name": "foundry-lite-business-system",
            "title": "업무 운영 화면",
            "description": "외부 업무 앱과 같은 화면 정의로 실제 업무 기록과 Action을 다룹니다.",
            "mimeType": BUSINESS_SYSTEM_MIME_TYPE,
            "_meta": _resource_meta(),
        }
    ]


def read_business_system_resource(params: Mapping[str, object]) -> dict[str, object]:
    uri = _required_text(params, "uri")
    if uri != BUSINESS_SYSTEM_RESOURCE_URI:
        raise NotFound("Ontology MCP business-system resource was not found", details={"uri": uri})
    template = _read_asset(_HTML_PATH, "apps/chatgpt-business-system-widget/index.html")
    runtime = _read_asset(_OSDK_PATH, "apps/chatgpt-business-system-widget/foundry-lite-mcp-osdk.js")
    if template.count(_OSDK_MARKER) != 1:
        raise ValidationFailed("Business-system MCP App is missing its high-level OSDK injection point")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": BUSINESS_SYSTEM_MIME_TYPE,
                "text": template.replace(_OSDK_MARKER, runtime),
                "_meta": _resource_meta(),
            }
        ]
    }


def _decorate_tool(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("Ontology MCP tool registry returned an invalid tool descriptor")
    descriptor = {str(key): item for key, item in value.items()}
    if descriptor.get("name") != "business_system.get":
        return descriptor
    meta = _mapping_or_empty(descriptor.get("_meta"))
    meta["ui"] = {**_mapping_or_empty(meta.get("ui")), "resourceUri": BUSINESS_SYSTEM_RESOURCE_URI}
    meta["openai/outputTemplate"] = BUSINESS_SYSTEM_RESOURCE_URI
    meta["openai/widgetAccessible"] = True
    descriptor["_meta"] = meta
    return descriptor


def _resource_meta() -> dict[str, object]:
    return {
        "ui": {"prefersBorder": True, "csp": {"connectDomains": [], "resourceDomains": []}},
        "openai/widgetDescription": "Interactive work screens backed by the application's governed Ontology.",
        "openai/widgetPrefersBorder": True,
    }


def _mapping_or_empty(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationFailed("Ontology MCP tool metadata must be an object")
    return {str(key): item for key, item in value.items()}


def _read_asset(path: Path, expected_asset: str) -> str:
    if not path.is_file():
        raise NotFound("Ontology MCP UI asset is not installed", details={"expectedAsset": expected_asset})
    return path.read_text(encoding="utf-8")


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"Ontology MCP {key} is required")
    return item.strip()


__all__ = [
    "BUSINESS_SYSTEM_RESOURCE_URI",
    "business_system_resource_descriptors",
    "decorate_ontology_tools",
    "read_business_system_resource",
]
