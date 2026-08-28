"""Compile one reviewed Domain OS into the shared AI FDE business-system contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, hash_json

JsonObject = Mapping[str, object]

_SCHEMA_VERSION = "foundry-lite-business-system-definition/v1"
_COMPONENT_CATALOG_VERSION = "foundry-lite-work-components/v1"


def build_business_system_definition(
    application_name: str,
    blueprint: JsonObject,
    consumer_osdk: JsonObject,
) -> dict[str, object]:
    """Return the single versioned contract shared by GPT, apps, and GPT Work."""

    records = _items(blueprint.get("records"), "domainOsBlueprint.records")
    workflow = _mapping(blueprint.get("workflow"), "domainOsBlueprint.workflow")
    actions = _items(workflow.get("actions"), "domainOsBlueprint.workflow.actions")
    roles = _items(blueprint.get("actorRoles"), "domainOsBlueprint.actorRoles")
    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "identity": _identity(application_name, blueprint, consumer_osdk),
        "businessModel": _business_model(blueprint, workflow),
        "access": _access_contract(roles, actions),
        "experience": _experience_contract(records, actions, roles, blueprint),
        "agentWork": _agent_work_contract(records, actions, blueprint, consumer_osdk),
        "deployment": _deployment_contract(consumer_osdk),
    }
    return {**payload, "definitionFingerprint": hash_json(payload)}


def require_business_system_definition(value: object) -> dict[str, object]:
    """Fail closed when a generated plan no longer carries the shared contract."""

    definition = _mapping(value, "businessSystemDefinition")
    fingerprint = definition.get("definitionFingerprint")
    payload = {name: item for name, item in definition.items() if name != "definitionFingerprint"}
    if definition.get("schemaVersion") != _SCHEMA_VERSION or fingerprint != hash_json(payload):
        raise FdePlatformToolError(
            "business_system_definition_invalid",
            "업무 시스템 정의서가 변경되었거나 버전을 확인할 수 없어 앱을 만들지 않았습니다.",
        )
    return definition


def _identity(application_name: str, blueprint: JsonObject, consumer_osdk: JsonObject) -> dict[str, object]:
    return {
        "applicationId": _text(consumer_osdk.get("applicationId"), "consumerOsdk.applicationId"),
        "name": application_name,
        "summary": _text(blueprint.get("summary"), "domainOsBlueprint.summary"),
    }


def _business_model(blueprint: JsonObject, workflow: JsonObject) -> dict[str, object]:
    return {
        "actors": list(_sequence(blueprint.get("actors"), "domainOsBlueprint.actors")),
        "records": _items(blueprint.get("records"), "domainOsBlueprint.records"),
        "lifecycleStates": list(_sequence(workflow.get("states"), "domainOsBlueprint.workflow.states")),
        "actions": _items(workflow.get("actions"), "domainOsBlueprint.workflow.actions"),
        "policies": _items(blueprint.get("policies"), "domainOsBlueprint.policies"),
        "functions": _items(blueprint.get("functions") or [], "domainOsBlueprint.functions"),
        "evidence": list(_sequence(blueprint.get("evidence"), "domainOsBlueprint.evidence")),
        "integrations": list(_sequence(blueprint.get("integrations"), "domainOsBlueprint.integrations")),
        "successMeasures": list(_sequence(blueprint.get("successMeasures"), "domainOsBlueprint.successMeasures")),
    }


def _access_contract(roles: list[dict[str, object]], actions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "roles": roles,
        "actionRules": [
            {
                "actionType": action["apiName"],
                "allowedRoles": list(_sequence(action.get("allowedRoles"), "action.allowedRoles")),
                "confirmation": "human_required" if action.get("requiresApproval") is True else "policy_governed",
            }
            for action in actions
        ],
        "defaultPolicy": "deny_unlisted",
    }


def _experience_contract(
    records: list[dict[str, object]],
    actions: list[dict[str, object]],
    roles: list[dict[str, object]],
    blueprint: JsonObject,
) -> dict[str, object]:
    primary = records[0]
    role_names = [str(role["role"]) for role in roles]
    screens = _screens(primary, records, actions, role_names, blueprint)
    screen_ids = [str(screen["id"]) for screen in screens]
    return {
        "componentCatalogVersion": _COMPONENT_CATALOG_VERSION,
        "screens": screens,
        "surfaces": [
            {"id": "chatgpt", "screenIds": screen_ids, "interactionMode": "conversation_embedded"},
            {"id": "external_app", "screenIds": screen_ids, "interactionMode": "authenticated_web"},
        ],
    }


def _screens(
    primary: JsonObject,
    records: list[dict[str, object]],
    actions: list[dict[str, object]],
    roles: list[str],
    blueprint: JsonObject,
) -> list[dict[str, object]]:
    screens = [
        _today_screen(primary, actions, roles),
        _record_screen(primary, actions, roles),
        _evidence_screen(primary, roles),
    ]
    if _items(blueprint.get("policies"), "domainOsBlueprint.policies"):
        screens.insert(2, _policy_screen(primary, actions, roles))
    if len(records) > 1:
        screens.append(_relationship_screen(records, roles))
    if _items(blueprint.get("functions") or [], "domainOsBlueprint.functions"):
        screens.insert(1, _kpi_screen(blueprint, roles))
    return screens


def _today_screen(primary: JsonObject, actions: list[dict[str, object]], roles: list[str]) -> dict[str, object]:
    object_type = str(primary["apiName"])
    return _screen(
        "today",
        "오늘 할 일",
        roles,
        [
            _component("work_queue", "업무 대기열", {"objectType": object_type, "pageSize": 50}),
            _component("action_panel", "다음 업무", {"actionTypes": _action_names(actions)}),
            _component("ai_suggestion_panel", "AI 업무 제안", {"objectType": object_type, "requiresEvidence": True}),
        ],
    )


def _record_screen(primary: JsonObject, actions: list[dict[str, object]], roles: list[str]) -> dict[str, object]:
    object_type = str(primary["apiName"])
    return _screen(
        "record",
        f"{primary['displayName']} 상세",
        roles,
        [
            _component("record_detail", "업무 정보", {"objectType": object_type}),
            _component("action_form", "업무 처리", {"actionTypes": _action_names(actions)}),
            _component("status_timeline", "상태 흐름", {"objectType": object_type}),
        ],
    )


def _policy_screen(primary: JsonObject, actions: list[dict[str, object]], roles: list[str]) -> dict[str, object]:
    components = [_component("policy_panel", "업무 규칙", {"objectType": primary["apiName"]})]
    if any(action.get("requiresApproval") is True for action in actions):
        components.append(_component("approval_inbox", "사람 확인 대기", {"actionTypes": _action_names(actions)}))
    return _screen("policy", "규칙과 승인", roles, components)


def _evidence_screen(primary: JsonObject, roles: list[str]) -> dict[str, object]:
    return _screen(
        "evidence",
        "변경 기록과 증거",
        roles,
        [
            _component("evidence_panel", "업무 증거", {"objectType": primary["apiName"]}),
            _component("audit_timeline", "변경 기록", {"objectType": primary["apiName"]}),
        ],
    )


def _relationship_screen(records: list[dict[str, object]], roles: list[str]) -> dict[str, object]:
    return _screen(
        "relationships",
        "업무 관계 탐색",
        roles,
        [_component("relationship_graph", "연결된 업무", {"objectTypes": _record_names(records)})],
    )


def _kpi_screen(blueprint: JsonObject, roles: list[str]) -> dict[str, object]:
    functions = _items(blueprint.get("functions") or [], "domainOsBlueprint.functions")
    return _screen(
        "kpi",
        "업무 현황",
        roles,
        [_component("kpi_summary", "핵심 지표", {"functionTypes": [row["apiName"] for row in functions]})],
    )


def _screen(screen_id: str, title: str, roles: list[str], components: list[dict[str, object]]) -> dict[str, object]:
    return {"id": screen_id, "title": title, "audienceRoles": roles, "components": components}


def _component(kind: str, title: str, binding: dict[str, object]) -> dict[str, object]:
    return {"id": kind.replace("_", "-"), "kind": kind, "title": title, "binding": binding}


def _agent_work_contract(
    records: list[dict[str, object]],
    actions: list[dict[str, object]],
    blueprint: JsonObject,
    consumer_osdk: JsonObject,
) -> dict[str, object]:
    return {
        "applicationId": consumer_osdk["applicationId"],
        "objectTypes": _record_names(records),
        "actionTypes": _agent_actions(actions),
        "functionTypes": [
            row["apiName"] for row in _items(blueprint.get("functions") or [], "domainOsBlueprint.functions")
        ],
        "capabilities": ["prioritize_work", "explain_with_evidence", "propose_action", "execute_governed_action"],
        "mutationPolicy": "use_defined_actions_only",
    }


def _agent_actions(actions: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "actionType": action["apiName"],
            "execution": "proposal_then_human" if action.get("requiresApproval") is True else "governed_direct",
            "allowedRoles": list(_sequence(action.get("allowedRoles"), "action.allowedRoles")),
        }
        for action in actions
    ]


def _deployment_contract(consumer_osdk: JsonObject) -> dict[str, object]:
    return {
        "applicationId": consumer_osdk["applicationId"],
        "releaseMode": "governed",
        "previewSurface": "chatgpt",
        "operatingSurface": "external_app",
        "requiredBeforeOperating": [
            "ontology_activated",
            "production_data_connected",
            "role_mapping_verified",
            "authenticated_session_verified",
            "human_release_approved",
        ],
    }


def _action_names(actions: list[dict[str, object]]) -> list[object]:
    return [action["apiName"] for action in actions]


def _record_names(records: list[dict[str, object]]) -> list[object]:
    return [record["apiName"] for record in records]


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _items(value: object, field: str) -> list[dict[str, object]]:
    sequence = _sequence(value, field)
    if not all(isinstance(item, Mapping) for item in sequence):
        raise FdePlatformToolError("schema_invalid", f"{field} must contain objects")
    return [{str(name): item for name, item in row.items()} for row in sequence if isinstance(row, Mapping)]


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FdePlatformToolError("schema_invalid", f"{field} must be non-empty text")
    return value.strip()


__all__ = ["build_business_system_definition", "require_business_system_definition"]
