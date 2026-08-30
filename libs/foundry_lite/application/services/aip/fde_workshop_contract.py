"""Typed construction helpers for the canonical Workshop application graph."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

JsonObject = Mapping[str, object]


def app_theme(application_name: str) -> dict[str, object]:
    presets = ("ocean", "indigo", "emerald", "amber", "graphite")
    digest = hashlib.sha256(application_name.encode("utf-8")).digest()[0]
    logo = "".join(part[:1] for part in application_name.split() if part)[:3]
    return {"preset": presets[digest % len(presets)], "brandName": application_name, "logoText": logo or "FL"}


def app_shell() -> dict[str, object]:
    return {"navigation": "sidebar", "density": "comfortable", "pageWidth": "wide", "showContextBar": True}


def page(
    page_id: str,
    name: str,
    is_default: bool,
    intent: str,
    sections: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": f"page-{page_id}",
        "name": name,
        "pageId": page_id,
        "isDefault": is_default,
        "backgroundColor": "transparent",
        "layoutDirection": "columns",
        "intent": intent,
        "sections": sections,
    }


def section(
    title: str,
    layout: str,
    widgets: list[dict[str, object]],
    span: int = 12,
    border: str = "none",
) -> dict[str, object]:
    return {
        "id": f"section-{identifier(title)}",
        "title": title,
        "layout": layout,
        "style": {"background": "transparent", "padding": "regular", "border": border},
        "span": span,
        "widgets": widgets,
    }


def widget(
    kind: str,
    title: str,
    object_type: str | None,
    overrides: JsonObject | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {"title": title, **({"objectApiName": object_type} if object_type else {})}
    config.update(dict(overrides or {}))
    return {
        "id": f"widget-{identifier(title)}",
        "kind": kind,
        "config": config,
        "objectApiName": object_type,
        "actionApiName": config.get("actionApiName"),
    }


def action_widget(title: str, object_type: str, actions: list[dict[str, object]]) -> dict[str, object]:
    action_names = [str(action["apiName"]) for action in actions]
    approvals = [str(action["apiName"]) for action in actions if action.get("requiresApproval") is True]
    return widget(
        "buttonGroup",
        title,
        object_type,
        {"actionApiNames": action_names, "humanApprovalActionApiNames": approvals},
    )


def header(application_name: str) -> dict[str, object]:
    return {
        "visible": True,
        "title": application_name,
        "slots": {
            name: section(f"헤더 {label}", "toolbar", [])
            for name, label in (("left", "좌측"), ("center", "중앙"), ("right", "우측"))
        },
    }


def visible_properties(record: JsonObject) -> list[str]:
    return [
        str(field["apiName"])
        for field in items(record.get("fields"))
        if field.get("apiName") != record.get("primaryKey")
    ]


def status_property(record: JsonObject) -> str | None:
    return _matching_property(record, {"status", "state"}, {"string"})


def date_property(record: JsonObject) -> str | None:
    return _matching_property(
        record, {"date", "time", "scheduled", "due", "appointment"}, {"date", "datetime", "timestamp", "time"}
    )


def numeric_property(record: JsonObject) -> str | None:
    return _matching_property(record, set(), {"integer", "int", "float", "double", "decimal", "number", "long"})


def secondary_category_property(record: JsonObject, excluded: str | None) -> str | None:
    for field in items(record.get("fields")):
        name = str(field.get("apiName") or "")
        if name != excluded and str(field.get("type") or "").lower() == "string" and name != record.get("primaryKey"):
            return name
    return None


def policy_markdown(policies: list[dict[str, object]]) -> str:
    if not policies:
        return "### 사람 확인\n중요한 업무는 실행 전에 담당자가 내용을 확인합니다."
    return "\n\n".join(f"### {row['name']}\n{row['statement']}" for row in policies)


def identifier(value: str) -> str:
    ascii_words = re.findall(r"[a-z0-9]+", value.lower().replace("_", "-"))
    if ascii_words:
        return "-".join(ascii_words)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): item for name, item in value.items()}


def items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [mapping(item) for item in value if isinstance(item, Mapping)]


def _matching_property(record: JsonObject, name_parts: set[str], types: set[str]) -> str | None:
    fields = items(record.get("fields"))
    named = next(
        (field for field in fields if any(part in str(field.get("apiName") or "").lower() for part in name_parts)),
        None,
    )
    typed = next((field for field in fields if str(field.get("type") or "").lower() in types), None)
    match = named or typed
    return str(match["apiName"]) if match else None


__all__ = [
    "action_widget",
    "app_shell",
    "app_theme",
    "date_property",
    "header",
    "items",
    "numeric_property",
    "page",
    "policy_markdown",
    "secondary_category_property",
    "section",
    "status_property",
    "visible_properties",
    "widget",
]
