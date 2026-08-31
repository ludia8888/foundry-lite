"""Commercial SaaS product shape compiled from one reviewed Domain OS."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

JsonObject = Mapping[str, object]

COMMERCIAL_PRODUCT_SCHEMA_VERSION = "foundry-lite-commercial-product/v1"


def build_commercial_product(
    application_name: str,
    blueprint: JsonObject,
    pages: Sequence[JsonObject],
) -> dict[str, object]:
    """Describe the audiences, modules, onboarding, and trust surface of the product."""

    roles = _items(blueprint.get("actorRoles"))
    workflow = _mapping(blueprint.get("workflow"))
    actions = _items(workflow.get("actions"))
    records = _items(blueprint.get("records"))
    policies = _items(blueprint.get("policies"))
    page_ids = _page_ids(pages)
    return {
        "schemaVersion": COMMERCIAL_PRODUCT_SCHEMA_VERSION,
        "productKind": "domain_operating_saas",
        "designStatus": "ready_for_business_review",
        "name": application_name,
        "audiences": _audiences(roles, actions, page_ids),
        "capabilityGroups": _capability_groups(records, actions, policies, page_ids),
        "onboarding": _onboarding(),
        "trustCenter": _trust_center(blueprint, policies),
    }


def _audiences(
    roles: list[dict[str, object]],
    actions: list[dict[str, object]],
    page_ids: list[str],
) -> list[dict[str, object]]:
    return [
        {
            "id": str(role["role"]),
            "name": str(role["displayName"]),
            "summary": _audience_summary(str(role["displayName"]), actions),
            "homePageId": "today",
            "pageIds": page_ids,
            "actionNames": _actor_action_names(str(role["displayName"]), actions),
        }
        for role in roles
    ]


def _audience_summary(actor: str, actions: list[dict[str, object]]) -> str:
    names = _actor_action_names(actor, actions)
    if not names:
        return "허용된 정보와 진행 상태를 확인합니다."
    preview = " · ".join(names[:3])
    return f"{preview}{' 외' if len(names) > 3 else ''} 업무를 담당합니다."


def _actor_action_names(actor: str, actions: list[dict[str, object]]) -> list[str]:
    return [
        str(action.get("displayName") or "업무 처리")
        for action in actions
        if actor in _strings(action.get("allowedActors"))
    ]


def _capability_groups(
    records: list[dict[str, object]],
    actions: list[dict[str, object]],
    policies: list[dict[str, object]],
    page_ids: list[str],
) -> list[dict[str, object]]:
    groups = [_operations_group(records, actions, page_ids)]
    groups.append(_insight_group(records, page_ids))
    if policies:
        groups.append(_governance_group(policies, page_ids))
    groups.append(_administration_group())
    return groups


def _operations_group(
    records: list[dict[str, object]],
    actions: list[dict[str, object]],
    page_ids: list[str],
) -> dict[str, object]:
    return {
        "id": "operations",
        "name": "일상 업무 운영",
        "description": "기록을 찾고, 상태를 확인하고, 허용된 다음 업무를 실행합니다.",
        "pageIds": _existing(page_ids, "today", "record", "workflow", "calendar"),
        "recordNames": [str(record.get("displayName") or "업무 기록") for record in records],
        "actionNames": [str(action.get("displayName") or "업무 처리") for action in actions],
    }


def _insight_group(records: list[dict[str, object]], page_ids: list[str]) -> dict[str, object]:
    return {
        "id": "insights",
        "name": "현황과 개선",
        "description": "업무량, 단계별 지연, 반복되는 예외를 같은 기록에서 분석합니다.",
        "pageIds": _existing(page_ids, "kpi", "workflow", "relationships"),
        "recordNames": [str(record.get("displayName") or "업무 기록") for record in records],
        "actionNames": [],
    }


def _governance_group(policies: list[dict[str, object]], page_ids: list[str]) -> dict[str, object]:
    return {
        "id": "governance",
        "name": "승인과 업무 증거",
        "description": "중요한 변경은 사람에게 확인받고 판단 근거와 변경 이력을 남깁니다.",
        "pageIds": _existing(page_ids, "policy", "evidence"),
        "recordNames": [],
        "actionNames": [str(policy.get("name") or "업무 규칙") for policy in policies],
    }


def _administration_group() -> dict[str, object]:
    return {
        "id": "administration",
        "name": "사용자와 서비스 운영",
        "description": "로그인 역할, 알림, 도움말, 배포 상태를 제품 수준에서 관리합니다.",
        "pageIds": [],
        "recordNames": [],
        "actionNames": [],
    }


def _onboarding() -> list[dict[str, str]]:
    return [
        _step("review-design", "업무 설계 확인", "사람·기록·규칙·업무 흐름을 검토합니다.", "design_ready"),
        _step(
            "connect-data", "실제 데이터 연결", "운영 데이터의 원본과 갱신 주기를 확인합니다.", "needs_configuration"
        ),
        _step(
            "verify-roles",
            "사용자 역할 확인",
            "직원과 고객이 볼 정보와 실행할 업무를 검증합니다.",
            "needs_configuration",
        ),
        _step("confirm-brand", "서비스 모습 확인", "이름, 색상, 로고, 도움말 문구를 최종 확인합니다.", "needs_review"),
        _step("release", "사람 승인 후 운영 시작", "실제 로그인과 업무 실행을 시험한 뒤 운영 URL을 엽니다.", "blocked"),
    ]


def _step(identifier: str, title: str, description: str, status: str) -> dict[str, str]:
    return {"id": identifier, "title": title, "description": description, "status": status}


def _trust_center(blueprint: JsonObject, policies: list[dict[str, object]]) -> dict[str, object]:
    return {
        "accessStatement": "로그인한 사용자의 역할과 허용 범위 안에서만 정보와 업무를 제공합니다.",
        "approvalStatement": "중요한 업무는 실행 전에 사람의 확인을 받고 결과를 다시 검증합니다.",
        "auditStatement": "담당자, 시각, 변경 전후 값, 판단 근거를 운영 증거로 남깁니다.",
        "evidenceNames": _strings(blueprint.get("evidence")),
        "policyNames": [str(policy.get("name") or "업무 규칙") for policy in policies],
    }


def _page_ids(pages: Sequence[JsonObject]) -> list[str]:
    return [str(page["pageId"]) for page in pages if isinstance(page.get("pageId"), str)]


def _existing(page_ids: list[str], *candidates: str) -> list[str]:
    return [candidate for candidate in candidates if candidate in page_ids]


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): item for name, item in value.items()}


def _items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = ["COMMERCIAL_PRODUCT_SCHEMA_VERSION", "build_commercial_product"]
