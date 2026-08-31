"""Compile one reviewed Domain OS into the shared AI FDE business-system contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, hash_json
from foundry_lite.application.services.aip.fde_workshop_definition import (
    WORKSHOP_COMPONENT_CATALOG_VERSION,
    build_workshop_app_definition,
)

JsonObject = Mapping[str, object]

_SCHEMA_VERSION = "foundry-lite-business-system-definition/v3"


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
        "experience": _experience_contract(application_name, blueprint),
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


def _experience_contract(application_name: str, blueprint: JsonObject) -> dict[str, object]:
    workshop_app = build_workshop_app_definition(application_name, blueprint)
    page_ids = [str(page["pageId"]) for page in _items(workshop_app.get("pages"), "workshop.pages")]
    return {
        "componentCatalogVersion": WORKSHOP_COMPONENT_CATALOG_VERSION,
        "workshopApp": workshop_app,
        "surfaces": [
            {"id": "chatgpt", "pageIds": page_ids, "runtime": "workshop"},
            {"id": "external_app", "pageIds": page_ids, "runtime": "workshop"},
            {"id": "workshop", "pageIds": page_ids, "runtime": "workshop"},
        ],
    }


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
