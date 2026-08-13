"""Compile non-developer analytical rules into governed Python OSDK Functions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError

JsonObject = Mapping[str, object]
MAX_DOMAIN_FUNCTIONS = 12
_AGGREGATIONS = frozenset({"count", "sum", "avg", "min", "max"})
_FILTER_OPERATORS = frozenset({"eq", "in", "gt", "gte", "lt", "lte", "contains"})
_NUMERIC_TYPES = frozenset({"integer", "float"})


def compile_domain_functions(
    brief: JsonObject,
    records: Sequence[JsonObject],
    actor_roles: Sequence[Mapping[str, str]],
) -> list[dict[str, object]]:
    """Validate bounded aggregation functions against the generated Ontology."""
    values = _mapping_items(brief.get("functions") or [], "domainBrief.functions", MAX_DOMAIN_FUNCTIONS)
    compiled = [_function(value, records, actor_roles, index) for index, value in enumerate(values)]
    api_names = [str(item["apiName"]) for item in compiled]
    if len(set(api_names)) != len(api_names):
        raise FdePlatformToolError("schema_invalid", "업무 계산 이름은 서로 달라야 합니다.")
    return compiled


def function_ontology_resource(function: JsonObject) -> dict[str, object]:
    """Render one immutable Python OSDK function definition."""
    return {
        "kind": "functionType",
        "definition": {
            "apiName": function["apiName"],
            "displayName": function["displayName"],
            "version": "1.0.0",
            "runtime": "python",
            "inputs": [],
            "output": {"type": "struct"},
            "timeoutSeconds": 60,
            "permissions": {"allowedRoles": function["allowedRoles"]},
            "definition": {
                "entrypoint": "compute",
                "source": _python_source(function),
            },
        },
    }


def function_application_resources(functions: Sequence[JsonObject]) -> list[dict[str, object]]:
    """Expose execute-only scopes for generated Functions."""
    return [
        {
            "resourceType": "function",
            "resourceApiName": row["apiName"],
            "scopes": [f"osdk:function:{row['apiName']}:execute"],
        }
        for row in functions
    ]


def _function(
    value: JsonObject,
    records: Sequence[JsonObject],
    actor_roles: Sequence[Mapping[str, str]],
    index: int,
) -> dict[str, object]:
    display_name = _required_text(value, "name", 120)
    record = _record(value, records)
    aggregation = _required_enum(value, "aggregation", _AGGREGATIONS)
    property_name = _metric_property(value, record, aggregation)
    actors, roles = _allowed_roles(value, actor_roles)
    return {
        "apiName": _api_name(value.get("apiName"), display_name, index),
        "displayName": display_name,
        "description": _optional_text(value, "description", 500),
        "recordApiName": record["apiName"],
        "aggregation": aggregation,
        "propertyApiName": property_name,
        "filters": _filters(value, record),
        "allowedActors": actors,
        "allowedRoles": roles,
    }


def _record(value: JsonObject, records: Sequence[JsonObject]) -> JsonObject:
    api_name = _required_text(value, "recordApiName", 64)
    for record in records:
        if record.get("apiName") == api_name:
            return record
    raise FdePlatformToolError("schema_invalid", f"업무 계산이 없는 기록을 참조합니다: {api_name}")


def _metric_property(value: JsonObject, record: JsonObject, aggregation: str) -> str | None:
    property_name = value.get("propertyApiName")
    if aggregation == "count" and property_name is None:
        return None
    if not isinstance(property_name, str) or not property_name:
        raise FdePlatformToolError("schema_invalid", f"{aggregation} 계산에는 숫자 정보가 필요합니다.")
    field = _field(record, property_name)
    if aggregation != "count" and field.get("type") not in _NUMERIC_TYPES:
        raise FdePlatformToolError("schema_invalid", f"{aggregation} 계산은 숫자 정보에만 사용할 수 있습니다.")
    return property_name


def _filters(value: JsonObject, record: JsonObject) -> list[dict[str, object]]:
    values = _mapping_items(value.get("filters") or [], "domainBrief.functions.filters", 12)
    compiled: list[dict[str, object]] = []
    for item in values:
        property_name = _required_text(item, "propertyApiName", 64)
        _field(record, property_name)
        operator = _required_enum(item, "operator", _FILTER_OPERATORS)
        if "value" not in item:
            raise FdePlatformToolError("schema_invalid", "업무 계산 필터에는 비교할 값이 필요합니다.")
        compiled.append({"propertyApiName": property_name, "operator": operator, "value": item["value"]})
    return compiled


def _allowed_roles(
    value: JsonObject,
    actor_roles: Sequence[Mapping[str, str]],
) -> tuple[list[str], list[str]]:
    actors = _text_items(value.get("allowedActors") or [], "domainBrief.functions.allowedActors", 12)
    roles_by_actor = {str(row["displayName"]): str(row["role"]) for row in actor_roles}
    unknown = sorted(set(actors) - roles_by_actor.keys())
    if unknown:
        raise FdePlatformToolError(
            "schema_invalid", f"업무 계산에 정의되지 않은 사용자가 있습니다: {', '.join(unknown)}"
        )
    if not actors:
        raise FdePlatformToolError("schema_invalid", "업무 계산을 볼 수 있는 사용자를 정해주세요.")
    return actors, [roles_by_actor[actor] for actor in actors]


def _python_source(function: JsonObject) -> str:
    object_type = str(function["recordApiName"])
    lines = [
        "from functions.api import function",
        "from ontology_sdk import FoundryClient",
        "",
        "@function",
        "def compute():",
        f"    records = FoundryClient().ontology.objects.{object_type}",
    ]
    for item in _mapping_items(function.get("filters"), "domainOsBlueprint.functions.filters", 12):
        operator = str(item["operator"])
        value = json.dumps(item["value"], ensure_ascii=False)
        lines.append(f"    records = records.where({item['propertyApiName']}={{'${operator}': {value}}})")
    property_name = function.get("propertyApiName")
    metric = {"name": "value", "function": function["aggregation"], "property": property_name}
    lines.append(f"    return records.aggregate(select={[metric]!r})")
    return "\n".join(lines) + "\n"


def _field(record: JsonObject, property_name: str) -> JsonObject:
    for field in _mapping_items(record.get("fields"), "record.fields", 24):
        if field.get("apiName") == property_name:
            return field
    raise FdePlatformToolError("schema_invalid", f"업무 계산이 없는 정보를 참조합니다: {property_name}")


def _api_name(value: object, display_name: str, index: int) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", value):
        return value
    words = re.findall(r"[A-Za-z0-9가-힣]+", display_name)
    ascii_words = [word for word in words if word.isascii()]
    return "".join(word[:1].upper() + word[1:] for word in ascii_words) or f"DomainFunction{index + 1}"


def _required_enum(value: JsonObject, field: str, allowed: frozenset[str]) -> str:
    item = _required_text(value, field, 64)
    if item not in allowed:
        raise FdePlatformToolError("schema_invalid", f"지원하지 않는 {field} 값입니다: {item}")
    return item


def _required_text(value: JsonObject, field: str, limit: int) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip() or len(item) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be non-empty text")
    return item.strip()


def _optional_text(value: JsonObject, field: str, limit: int) -> str:
    item = value.get(field)
    if item is None:
        return ""
    if not isinstance(item, str) or len(item) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be text")
    return item.strip()


def _mapping_items(value: object, field: str, limit: int) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list")
    if len(value) > limit or not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", f"{field} exceeds its bounded object-list contract")
    return [{str(name): item for name, item in row.items()} for row in value if isinstance(row, Mapping)]


def _text_items(value: object, field: str, limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be a bounded text list")
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(items) != len(value):
        raise FdePlatformToolError("schema_invalid", f"{field} must contain non-empty text")
    return items
