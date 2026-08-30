"""Compile an AI FDE business brief into the canonical Workshop app contract."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.aip.fde_workshop_contract import (
    action_widget,
    app_shell,
    app_theme,
    date_property,
    header,
    items,
    numeric_property,
    page,
    policy_markdown,
    secondary_category_property,
    section,
    status_property,
    visible_properties,
    widget,
)

JsonObject = Mapping[str, object]

WORKSHOP_COMPONENT_CATALOG_VERSION = "foundry-lite-workshop-components/v3"
WORKSHOP_METADATA_KIND = "foundry-lite.workshop.app-definition"
WORKSHOP_METADATA_SCHEMA_VERSION = 3


def build_workshop_app_definition(
    application_name: str,
    blueprint: JsonObject,
) -> dict[str, object]:
    """Return the one responsive Workshop definition rendered by every surface."""

    records = items(blueprint.get("records"))
    workflow = _mapping(blueprint.get("workflow"))
    actions = items(workflow.get("actions"))
    policies = items(blueprint.get("policies"))
    primary = records[0]
    pages = _pages(primary, records, actions, policies, blueprint)
    return {
        "name": application_name,
        "purpose": str(blueprint.get("summary") or "업무를 한곳에서 처리합니다."),
        "theme": app_theme(application_name),
        "shell": app_shell(),
        "header": header(application_name),
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
    if items(blueprint.get("functions") or []):
        pages.insert(1, _kpi_page(primary))
    if date_property(primary):
        pages.append(_calendar_page(primary))
    if policies or any(action.get("requiresApproval") is True for action in actions):
        pages.append(_policy_page(primary, actions, policies))
    pages.append(_evidence_page(primary))
    if len(records) > 1:
        pages.append(_relationship_page(records))
    return pages


def _work_page(primary: JsonObject, actions: list[dict[str, object]]) -> dict[str, object]:
    object_type = str(primary["apiName"])
    properties = visible_properties(primary)
    status = status_property(primary)
    queue_widgets = [widget("objectTable", "업무 목록", object_type, {"propertyApiNames": properties})]
    if status:
        queue_widgets.append(widget("kanban", "상태별 보드", object_type, {"groupByProperty": status}))
    sections = [
        section("업무 요약", "toolbar", _summary_widgets(object_type, status), 12),
        section("업무 찾기", "flow", [_filter_widget(object_type, status)], 3, "bordered"),
        section("처리 대기열", "tabs", queue_widgets, 6, "shadow"),
        section(
            "선택한 업무",
            "flow",
            [
                widget("objectDetail", "업무 정보", object_type, {"propertyApiNames": properties}),
                action_widget("다음 업무", object_type, actions),
            ],
            3,
            "bordered",
        ),
    ]
    return page("today", "오늘 할 일", True, "workbench", sections)


def _summary_widgets(object_type: str, status: str | None) -> list[dict[str, object]]:
    widgets = [
        widget("objectSetTitle", "업무 현황", object_type),
        widget("metricCard", "현재 업무", object_type, {"metric": "count", "unit": "건"}),
        widget("searchBar", "업무 검색", object_type),
    ]
    if status:
        widgets.append(widget("statusTracker", "상태 흐름", object_type, {"groupByProperty": status}))
    return widgets


def _filter_widget(object_type: str, status: str | None) -> dict[str, object]:
    properties = [status] if status else []
    return widget("filterList", "빠른 필터", object_type, {"propertyApiNames": properties})


def _detail_page(primary: JsonObject, actions: list[dict[str, object]]) -> dict[str, object]:
    object_type = str(primary["apiName"])
    properties = visible_properties(primary)
    sections = [
        section("업무 목록", "flow", [widget("objectList", "최근 업무", object_type)], 3, "bordered"),
        section(
            "전체 정보",
            "flow",
            [widget("objectDetail", "상세 정보", object_type, {"propertyApiNames": properties})],
            6,
            "shadow",
        ),
        section("업무 처리", "flow", [action_widget("가능한 업무", object_type, actions)], 3, "bordered"),
    ]
    return page("record", f"{primary['displayName']} 상세", False, "records", sections)


def _calendar_page(primary: JsonObject) -> dict[str, object]:
    object_type = str(primary["apiName"])
    date = date_property(primary)
    sections = [
        section("일정", "flow", [widget("calendar", "업무 캘린더", object_type, {"dateProperty": date})], 8, "shadow"),
        section(
            "일정 상세",
            "flow",
            [widget("timeline", "다가오는 업무", object_type, {"dateProperty": date})],
            4,
            "bordered",
        ),
    ]
    return page("calendar", "일정", False, "overview", sections)


def _policy_page(
    primary: JsonObject,
    actions: list[dict[str, object]],
    policies: list[dict[str, object]],
) -> dict[str, object]:
    object_type = str(primary["apiName"])
    approvals = [action for action in actions if action.get("requiresApproval") is True]
    sections = [
        section(
            "업무 규칙",
            "flow",
            [widget("markdown", "적용 중인 규칙", None, {"text": policy_markdown(policies)})],
            4,
            "bordered",
        ),
        section("확인 대기", "flow", [widget("objectTable", "사람 확인 대상", object_type)], 4, "shadow"),
        section("검토 후 실행", "flow", [action_widget("승인 업무", object_type, approvals or actions)], 4, "bordered"),
    ]
    return page("policy", "규칙과 승인", False, "governance", sections)


def _evidence_page(primary: JsonObject) -> dict[str, object]:
    object_type = str(primary["apiName"])
    date = date_property(primary)
    status = status_property(primary)
    sections = [
        section("업무 기록", "flow", [widget("objectTable", "변경된 업무", object_type)], 8, "shadow"),
        section("선택한 기록", "flow", [widget("objectDetail", "근거와 정보", object_type)], 4, "bordered"),
    ]
    if date:
        sections.append(
            section("시간 순서", "flow", [widget("timeline", "상태 타임라인", object_type, {"dateProperty": date})], 12)
        )
    elif status:
        sections.append(
            section(
                "상태 분포",
                "flow",
                [widget("statusTracker", "현재 상태", object_type, {"groupByProperty": status})],
                12,
            )
        )
    return page("evidence", "변경 기록과 증거", False, "evidence", sections)


def _relationship_page(records: list[dict[str, object]]) -> dict[str, object]:
    sections = [
        section(
            str(record["displayName"]),
            "flow",
            [widget("objectTable", f"{record['displayName']} 목록", str(record["apiName"]))],
            4 if len(records) <= 3 else 6,
            "bordered",
        )
        for record in records
    ]
    return page("relationships", "업무 개념 탐색", False, "relationships", sections)


def _kpi_page(primary: JsonObject) -> dict[str, object]:
    object_type = str(primary["apiName"])
    status = status_property(primary)
    numeric = numeric_property(primary)
    series = secondary_category_property(primary, status)
    metrics = [{"label": "전체 업무", "metric": "count", "unit": "건"}]
    if numeric:
        metrics.append({"label": "합계", "metric": "sum", "property": numeric})
    sections = [
        section("핵심 지표", "flow", [widget("metricCard", "운영 지표", object_type, {"metrics": metrics})], 12)
    ]
    if status:
        sections.extend(_analytic_sections(object_type, status, series, numeric))
    return page("kpi", "업무 현황", False, "overview", sections)


def _analytic_sections(
    object_type: str,
    status: str,
    series: str | None,
    numeric: str | None,
) -> list[dict[str, object]]:
    metric = "sum" if numeric else "count"
    config = {"groupByProperty": status, "metric": metric, "metricProperty": numeric}
    return [
        section("상태 차트", "flow", [widget("barChart", "상태별 업무", object_type, config)], 6, "shadow"),
        section(
            "업무 비중",
            "flow",
            [widget("pieChart", "상태 비중", object_type, {"groupByProperty": status})],
            6,
            "bordered",
        ),
        section(
            "교차 분석",
            "flow",
            [widget("pivotTable", "운영 피벗", object_type, {**config, "seriesProperty": series})],
            12,
            "bordered",
        ),
    ]


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): item for name, item in value.items()}


__all__ = [
    "WORKSHOP_COMPONENT_CATALOG_VERSION",
    "WORKSHOP_METADATA_KIND",
    "WORKSHOP_METADATA_SCHEMA_VERSION",
    "build_workshop_app_definition",
]
