"""Compile an AI FDE business brief into the canonical Workshop app contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

JsonObject = Mapping[str, object]

WORKSHOP_COMPONENT_CATALOG_VERSION = "foundry-lite-workshop-components/v2"
WORKSHOP_METADATA_KIND = "foundry-lite.workshop.app-definition"
WORKSHOP_METADATA_SCHEMA_VERSION = 2


def build_workshop_app_definition(
    application_name: str,
    blueprint: JsonObject,
) -> dict[str, object]:
    """Return the one Workshop definition rendered by every human-facing surface."""

    records = _items(blueprint.get("records"))
    workflow = _mapping(blueprint.get("workflow"))
    actions = _items(workflow.get("actions"))
    policies = _items(blueprint.get("policies"))
    primary = records[0]
    pages = _pages(primary, records, actions, policies, blueprint)
    return {
        "name": application_name,
        "purpose": str(blueprint.get("summary") or "업무를 한곳에서 처리합니다."),
        "header": _header(application_name),
        "page": pages[0],
        "pages": pages,
        "overlays": [],
        "variables": [],
        "savedAt": None,
        "version": 1,
    }


def _pages(
    primary: JsonObject,
    records: list[dict[str, object]],
    actions: list[dict[str, object]],
    policies: list[dict[str, object]],
    blueprint: JsonObject,
) -> list[dict[str, object]]:
    pages = [_work_page(primary, actions), _detail_page(primary, actions)]
    if policies or any(action.get("requiresApproval") is True for action in actions):
        pages.append(_policy_page(primary, actions, policies))
    pages.append(_evidence_page(primary))
    if len(records) > 1:
        pages.append(_relationship_page(records))
    if _items(blueprint.get("functions") or []):
        pages.insert(1, _kpi_page(primary))
    return pages


def _work_page(primary: JsonObject, actions: list[dict[str, object]]) -> dict[str, object]:
    object_type = str(primary["apiName"])
    properties = _visible_properties(primary)
    return _page(
        "today",
        "오늘 할 일",
        True,
        [
            _section(
                "업무 찾기",
                "toolbar",
                [
                    _widget("objectSetTitle", "업무 현황", object_type),
                    _widget("searchBar", "업무 검색", object_type),
                    _widget("metricCard", "현재 업무", object_type, {"metric": "count", "unit": "건"}),
                ],
            ),
            _section(
                "업무 처리",
                "columns",
                [
                    _widget("objectTable", "업무 대기열", object_type, {"propertyApiNames": properties}),
                    _widget("objectDetail", "업무 정보", object_type, {"propertyApiNames": properties}),
                    _action_widget("다음 업무", object_type, actions),
                ],
            ),
        ],
    )


def _detail_page(primary: JsonObject, actions: list[dict[str, object]]) -> dict[str, object]:
    object_type = str(primary["apiName"])
    return _page(
        "record",
        f"{primary['displayName']} 상세",
        False,
        [
            _section(
                "선택한 업무",
                "columns",
                [
                    _widget("objectList", "업무 목록", object_type),
                    _widget(
                        "objectDetail", "상세 정보", object_type, {"propertyApiNames": _visible_properties(primary)}
                    ),
                    _action_widget("업무 처리", object_type, actions),
                ],
            )
        ],
    )


def _policy_page(
    primary: JsonObject,
    actions: list[dict[str, object]],
    policies: list[dict[str, object]],
) -> dict[str, object]:
    object_type = str(primary["apiName"])
    approvals = [action for action in actions if action.get("requiresApproval") is True]
    return _page(
        "policy",
        "규칙과 승인",
        False,
        [
            _section(
                "업무 규칙",
                "columns",
                [
                    _widget("markdown", "적용 중인 규칙", None, {"text": _policy_markdown(policies)}),
                    _widget("objectTable", "사람 확인 대상", object_type),
                    _action_widget("사람 확인 후 실행", object_type, approvals or actions),
                ],
            ),
        ],
    )


def _evidence_page(primary: JsonObject) -> dict[str, object]:
    object_type = str(primary["apiName"])
    return _page(
        "evidence",
        "변경 기록과 증거",
        False,
        [
            _section(
                "업무 증거",
                "columns",
                [
                    _widget("objectTable", "업무 기록", object_type),
                    _widget("objectDetail", "선택한 기록", object_type),
                    _widget("timeline", "상태 타임라인", object_type),
                ],
            )
        ],
    )


def _relationship_page(records: list[dict[str, object]]) -> dict[str, object]:
    names = "\n".join(f"- {record['displayName']} (`{record['apiName']}`)" for record in records)
    return _page(
        "relationships",
        "업무 관계 탐색",
        False,
        [
            _section(
                "업무 개념",
                "flow",
                [
                    _widget("markdown", "연결된 업무", None, {"text": f"### 관리하는 업무\n{names}"}),
                ],
            )
        ],
    )


def _kpi_page(primary: JsonObject) -> dict[str, object]:
    object_type = str(primary["apiName"])
    return _page(
        "kpi",
        "업무 현황",
        False,
        [
            _section(
                "핵심 지표",
                "toolbar",
                [
                    _widget("metricCard", "전체 업무", object_type, {"metric": "count", "unit": "건"}),
                    _widget("barChart", "상태별 업무", object_type, {"groupByProperty": "status"}),
                ],
            )
        ],
    )


def _page(
    page_id: str,
    name: str,
    is_default: bool,
    sections: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": f"page-{page_id}",
        "name": name,
        "pageId": page_id,
        "isDefault": is_default,
        "backgroundColor": "transparent",
        "layoutDirection": "columns",
        "sections": sections,
    }


def _section(
    title: str,
    layout: str,
    widgets: list[dict[str, object]],
) -> dict[str, object]:
    section_id = _identifier(title)
    return {
        "id": f"section-{section_id}",
        "title": title,
        "layout": layout,
        "style": {"background": "transparent", "padding": "regular", "border": "none"},
        "widgets": widgets,
    }


def _widget(
    kind: str,
    title: str,
    object_type: str | None,
    overrides: JsonObject | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {
        "title": title,
        **({"objectApiName": object_type} if object_type else {}),
    }
    config.update(dict(overrides or {}))
    return {
        "id": f"widget-{_identifier(title)}",
        "kind": kind,
        "config": config,
        "objectApiName": object_type,
        "actionApiName": config.get("actionApiName"),
    }


def _action_widget(
    title: str,
    object_type: str,
    actions: list[dict[str, object]],
) -> dict[str, object]:
    action_names = [str(action["apiName"]) for action in actions]
    approvals = [str(action["apiName"]) for action in actions if action.get("requiresApproval") is True]
    return _widget(
        "buttonGroup",
        title,
        object_type,
        {"actionApiNames": action_names, "humanApprovalActionApiNames": approvals},
    )


def _header(application_name: str) -> dict[str, object]:
    return {
        "visible": True,
        "title": application_name,
        "slots": {
            name: _section(f"헤더 {label}", "toolbar", [])
            for name, label in (("left", "좌측"), ("center", "중앙"), ("right", "우측"))
        },
    }


def _visible_properties(primary: JsonObject) -> list[str]:
    fields = _items(primary.get("fields"))
    return [str(field["apiName"]) for field in fields if field.get("apiName") != primary.get("primaryKey")]


def _policy_markdown(policies: list[dict[str, object]]) -> str:
    if not policies:
        return "### 사람 확인\n중요한 업무는 실행 전에 담당자가 내용을 확인합니다."
    return "\n\n".join(f"### {row['name']}\n{row['statement']}" for row in policies)


def _identifier(value: str) -> str:
    return "-".join(part for part in value.lower().replace("_", "-").split() if part) or "item"


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): item for name, item in value.items()}


def _items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


__all__ = [
    "WORKSHOP_COMPONENT_CATALOG_VERSION",
    "WORKSHOP_METADATA_KIND",
    "WORKSHOP_METADATA_SCHEMA_VERSION",
    "build_workshop_app_definition",
]
