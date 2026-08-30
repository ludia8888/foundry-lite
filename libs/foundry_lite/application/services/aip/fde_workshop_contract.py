"""Typed construction helpers for the canonical Workshop application graph."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

JsonObject = Mapping[str, object]

_STATUS_WORDS = {
    "ACTIVE": "진행 중",
    "APPROVED": "승인 완료",
    "BLOCKED": "진행 차단",
    "CANCELLED": "취소됨",
    "CLOSED": "종료",
    "COLLECTING": "자료 수집 중",
    "COMPLETED": "완료",
    "CONFIRMED": "확정",
    "CONSENT": "동의",
    "CONTRACTED": "계약 완료",
    "DRAFTED": "초안 완료",
    "FILED": "제출 완료",
    "HELD": "임시 확보",
    "NEW": "신규",
    "OFFERED": "제안 완료",
    "PENDING": "확인 대기",
    "QUALIFIED": "확인 완료",
    "READY": "준비 완료",
    "RECONCILING": "대사 중",
    "REJECTED": "반려",
    "REPORTED": "접수됨",
    "REQUESTED": "요청됨",
    "REVIEW": "검토 중",
    "SCHEDULED": "일정 확정",
    "SEATED": "이용 중",
    "TRIAGED": "분류 완료",
}


def app_theme(application_name: str) -> dict[str, object]:
    presets = ("ocean", "indigo", "emerald", "amber", "graphite")
    digest = hashlib.sha256(application_name.encode("utf-8")).digest()[0]
    logo = "".join(part[:1] for part in application_name.split() if part)[:3]
    return {"preset": presets[digest % len(presets)], "brandName": application_name, "logoText": logo or "FL"}


def app_shell() -> dict[str, object]:
    return {"navigation": "sidebar", "density": "comfortable", "pageWidth": "wide", "showContextBar": True}


def app_presentation(blueprint: JsonObject) -> dict[str, object]:
    records = items(blueprint.get("records"))
    workflow = mapping(blueprint.get("workflow"))
    actions = items(workflow.get("actions"))
    states = strings(workflow.get("states"))
    return {
        "locale": "ko-KR",
        "objectTypeNames": _object_type_names(records),
        "propertyNames": _property_names(records),
        "actionNames": _action_names(actions),
        "statusLabels": {state: _status_presentation(state) for state in states},
        "booleanLabels": {"trueLabel": "예", "falseLabel": "아니요", "emptyLabel": "정보 없음"},
        "feedback": _feedback_copy(),
        "chrome": {
            "workspaceLabel": "업무 운영 공간",
            "helpLabel": "도움말",
            "notificationLabel": "알림",
            "userLabel": "내 계정",
        },
        "roles": strings(blueprint.get("actors")),
        "showTechnicalDetails": False,
    }


def _object_type_names(records: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(record["apiName"]): str(record.get("displayName") or record.get("name") or "업무 기록")
        for record in records
    }


def _property_names(records: list[dict[str, object]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for record in records:
        object_name = str(record["apiName"])
        for field in items(record.get("fields")):
            api_name = str(field["apiName"])
            label = str(field.get("displayName") or field.get("name") or _human_status(api_name))
            names[f"{object_name}.{api_name}"] = label
    return names


def _action_names(actions: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(action["apiName"]): str(
            action.get("displayName") or action.get("name") or _human_status(str(action["apiName"]))
        )
        for action in actions
    }


def _status_presentation(state: str) -> dict[str, str]:
    return {"label": _human_status(state), "intent": _status_intent(state)}


def _human_status(value: str) -> str:
    tokens = [part for part in re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).upper().split("_") if part]
    translated = [_STATUS_WORDS.get(token, token.lower()) for token in tokens]
    return " ".join(translated) or "상태 미지정"


def _status_intent(value: str) -> str:
    normalized = value.upper()
    if re.search(r"APPROVED|ACTIVE|CONFIRMED|COMPLETED|CLOSED|FILED|SUCCESS", normalized):
        return "success"
    if re.search(r"PENDING|REVIEW|WAIT|READY|SCHEDULED|DRAFT", normalized):
        return "warning"
    if re.search(r"REJECT|BLOCK|ERROR|FAIL|CANCEL|DENIED", normalized):
        return "danger"
    if re.search(r"NEW|REPORTED|REQUESTED|OPEN|COLLECTING", normalized):
        return "info"
    return "neutral"


def _feedback_copy() -> dict[str, str]:
    return {
        "loadingTitle": "업무를 불러오고 있습니다",
        "loadingDescription": "허용된 최신 기록을 안전하게 확인하고 있습니다.",
        "emptyTitle": "표시할 업무가 없습니다",
        "emptyDescription": "검색 조건을 바꾸거나 새 업무를 시작해 보세요.",
        "errorTitle": "업무를 불러오지 못했습니다",
        "errorDescription": "연결 상태를 확인한 뒤 다시 시도해 주세요.",
        "forbiddenTitle": "이 업무를 볼 권한이 없습니다",
        "forbiddenDescription": "필요한 경우 관리자에게 접근 권한을 요청해 주세요.",
        "approvalTitle": "사람의 확인이 필요합니다",
        "successTitle": "업무를 완료했습니다",
    }


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
        and not re.search(r"(^id$|_id$|Id$)", str(field.get("apiName") or ""))
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


def strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str)]


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
    "app_presentation",
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
