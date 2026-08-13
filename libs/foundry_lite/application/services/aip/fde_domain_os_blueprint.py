"""Validated, human-readable Domain OS blueprint for AI FDE Pilot."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_domain_os_functions import (
    compile_domain_functions,
    function_application_resources,
    function_ontology_resource,
)
from foundry_lite.application.services.aip.fde_domain_os_policy import (
    compile_domain_policies,
    policy_preconditions,
)
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError

JsonObject = Mapping[str, object]
_MAX_ACTORS = 12
_MAX_RECORDS = 8
_MAX_FIELDS = 20
_MAX_STATES = 16
_MAX_ACTIONS = 20
_MAX_POLICIES = 20
_FIELD_TYPES = frozenset({"string", "integer", "float", "boolean", "date", "timestamp"})


def build_domain_os_blueprint(arguments: JsonObject) -> dict[str, object]:
    """Normalize one detailed business description into an executable plan contract."""

    brief = _mapping(arguments.get("domainBrief"), "domainBrief")
    actors = _text_items(brief.get("actors") or [], "domainBrief.actors", _MAX_ACTORS)
    actor_roles = _actor_roles(_required_text(arguments, "applicationName", 255), actors)
    states = _states(brief)
    records = _records(brief, states)
    primary_record = records[0] if records else None
    actions = _actions(brief, primary_record, states, actor_roles)
    policies = compile_domain_policies(brief, primary_record, actions)
    functions = compile_domain_functions(brief, records, actor_roles)
    evidence = _text_items(brief.get("evidence") or [], "domainBrief.evidence", 20)
    gaps = _readiness_gaps(actors, records, states, actions, policies, evidence)
    return {
        "schemaVersion": "foundry-lite-domain-os-blueprint/v1",
        "summary": _required_text(arguments, "domainDescription", 10_000),
        "actors": actors,
        "actorRoles": actor_roles,
        "records": records,
        "workflow": {"states": states, "actions": actions},
        "policies": policies,
        "functions": functions,
        "evidence": evidence,
        "integrations": _text_items(brief.get("integrations") or [], "domainBrief.integrations", 20),
        "successMeasures": _text_items(brief.get("successMeasures") or [], "domainBrief.successMeasures", 20),
        "screens": _screens(primary_record, policies),
        "readiness": _readiness(gaps),
    }


def require_ready_blueprint(value: object) -> dict[str, object]:
    """Reject generation until the human-facing blueprint has no unresolved gap."""

    blueprint = _mapping(value, "domainOsBlueprint")
    readiness = _mapping(blueprint.get("readiness"), "domainOsBlueprint.readiness")
    if readiness.get("isReady") is not True:
        raise FdePlatformToolError(
            "domain_blueprint_incomplete",
            "업무 설계에 빈칸이 남아 있어 앱을 만들지 않았습니다. 보완 질문에 답한 뒤 계획을 다시 만드세요.",
        )
    return blueprint


def ontology_resources(blueprint: JsonObject, dataset_ref: str) -> list[dict[str, object]]:
    """Compile blueprint records and actions to branch-only Ontology definitions."""

    records = _mapping_items(blueprint.get("records"), "domainOsBlueprint.records", _MAX_RECORDS)
    actions = _workflow_actions(blueprint)
    policies = _mapping_items(blueprint.get("policies"), "domainOsBlueprint.policies", _MAX_POLICIES)
    functions = _mapping_items(blueprint.get("functions") or [], "domainOsBlueprint.functions", 12)
    resources = [_object_resource(record, _record_dataset_ref(dataset_ref, record)) for record in records]
    resources.extend(_action_resource(action, policies) for action in actions)
    resources.extend(function_ontology_resource(function) for function in functions)
    return resources


def application_resources(blueprint: JsonObject) -> list[dict[str, object]]:
    """Compile least-privilege app resource scopes from the same blueprint."""

    records = _mapping_items(blueprint.get("records"), "domainOsBlueprint.records", _MAX_RECORDS)
    actions = _workflow_actions(blueprint)
    functions = _mapping_items(blueprint.get("functions") or [], "domainOsBlueprint.functions", 12)
    object_rows = [
        {
            "resourceType": "object",
            "resourceApiName": row["apiName"],
            "scopes": [f"osdk:object:{row['apiName']}:read"],
        }
        for row in records
    ]
    action_rows = [
        {
            "resourceType": "action",
            "resourceApiName": row["apiName"],
            "scopes": [f"osdk:action:{row['apiName']}:execute"],
        }
        for row in actions
    ]
    function_rows = function_application_resources(functions)
    return [*object_rows, *action_rows, *function_rows]


def seed_plan(slug: str, blueprint: JsonObject) -> dict[str, object]:
    """Create one independent deterministic seed dataset per generated record."""

    records = _mapping_items(blueprint.get("records"), "domainOsBlueprint.records", _MAX_RECORDS)
    states = _workflow_states(blueprint)
    base_ref = f"seed.{slug}"
    datasets = [_record_seed_dataset(base_ref, record, states) for record in records]
    primary = datasets[0] if datasets else {"datasetRef": base_ref, "primaryKey": ["id"], "rows": []}
    return {**primary, "datasets": datasets}


def _record_seed_dataset(base_ref: str, record: JsonObject, states: list[str]) -> dict[str, object]:
    primary_key = str(record["primaryKey"])
    row = {
        _snake(str(field["apiName"])): _sample_value(field, states)
        for field in _mapping_items(record.get("fields"), "record.fields", _MAX_FIELDS + 3)
    }
    primary_key_column = _snake(primary_key)
    row[primary_key_column] = f"sample-{_snake(str(record['apiName']))}-1"
    return {
        "recordApiName": record["apiName"],
        "datasetRef": _record_dataset_ref(base_ref, record),
        "primaryKey": [primary_key_column],
        "rows": [row],
    }


def _record_dataset_ref(base_ref: str, record: JsonObject) -> str:
    if record.get("isPrimary") is True:
        return base_ref
    return f"{base_ref}_{_snake(str(record['apiName']))}"


def _records(brief: JsonObject, states: list[str]) -> list[dict[str, object]]:
    values = _mapping_items(brief.get("records") or [], "domainBrief.records", _MAX_RECORDS)
    records = [_record(value, index, states) for index, value in enumerate(values)]
    api_names = [row["apiName"] for row in records]
    if len(set(api_names)) != len(api_names):
        raise FdePlatformToolError("schema_invalid", "업무 기록 이름은 서로 달라야 합니다.")
    return records


def _record(value: JsonObject, index: int, states: list[str]) -> dict[str, object]:
    display_name = _required_text(value, "name", 120)
    api_name = _optional_api_name(value, "apiName") or _pascal(display_name, f"DomainRecord{index + 1}")
    primary_key = f"{api_name[:1].lower() + api_name[1:]}Id"
    custom_fields = _mapping_items(value.get("fields") or [], "domainBrief.records.fields", _MAX_FIELDS)
    fields = [_field(item, field_index) for field_index, item in enumerate(custom_fields)]
    reserved = {primary_key, "name", "status"}
    fields = [field for field in fields if field["apiName"] not in reserved]
    field_names = [field["apiName"] for field in fields]
    if len(set(field_names)) != len(field_names):
        raise FdePlatformToolError("schema_invalid", f"{display_name}의 정보 이름은 서로 달라야 합니다.")
    return {
        "apiName": api_name,
        "displayName": display_name,
        "description": _optional_text(value, "description", 500),
        "primaryKey": primary_key,
        "isPrimary": index == 0,
        "fields": [
            {"apiName": primary_key, "displayName": f"{display_name} ID", "type": "string", "required": True},
            {"apiName": "name", "displayName": "이름", "type": "string", "required": True},
            {"apiName": "status", "displayName": "현재 상태", "type": "string", "required": True},
            *fields,
        ],
        "allowedStates": states if index == 0 else ["ACTIVE", "INACTIVE"],
    }


def _field(value: JsonObject, index: int) -> dict[str, object]:
    display_name = _required_text(value, "name", 120)
    field_type = str(value.get("type") or "string")
    if field_type not in _FIELD_TYPES:
        raise FdePlatformToolError("schema_invalid", f"지원하지 않는 필드 형식입니다: {field_type}")
    return {
        "apiName": _optional_api_name(value, "apiName") or _camel(display_name, f"field{index + 1}"),
        "displayName": display_name,
        "type": field_type,
        "required": value.get("required") is True,
        "description": _optional_text(value, "description", 300),
    }


def _states(brief: JsonObject) -> list[str]:
    values = _text_items(brief.get("lifecycleStates") or [], "domainBrief.lifecycleStates", _MAX_STATES)
    normalized = [_constant(value) for value in values]
    return list(dict.fromkeys(normalized))


def _actions(
    brief: JsonObject,
    primary_record: JsonObject | None,
    states: list[str],
    actor_roles: list[dict[str, str]],
) -> list[dict[str, object]]:
    values = _mapping_items(brief.get("actions") or [], "domainBrief.actions", _MAX_ACTIONS)
    actions = [_action(value, primary_record, states, actor_roles, index) for index, value in enumerate(values)]
    api_names = [row["apiName"] for row in actions]
    if len(set(api_names)) != len(api_names):
        raise FdePlatformToolError("schema_invalid", "업무 버튼 이름은 서로 달라야 합니다.")
    return actions


def _action(
    value: JsonObject,
    primary_record: JsonObject | None,
    states: list[str],
    actor_roles: list[dict[str, str]],
    index: int,
) -> dict[str, object]:
    display_name = _required_text(value, "name", 120)
    from_states, to_state = _action_transition(value, states)
    allowed_actors, allowed_roles = _action_access(value, actor_roles)
    information = _text_items(value.get("requiredInformation") or [], "action.requiredInformation", 12)
    return {
        "apiName": _optional_api_name(value, "apiName") or _pascal(display_name, f"DomainAction{index + 1}"),
        "displayName": display_name,
        "description": _optional_text(value, "description", 500),
        "targetRecord": None if primary_record is None else primary_record["apiName"],
        "fromStates": from_states,
        "toState": to_state,
        "requiredInformation": information,
        "parameters": _action_parameters(information),
        "allowedActors": allowed_actors,
        "allowedRoles": allowed_roles,
        "requiresApproval": value.get("requiresApproval") is True,
    }


def _action_transition(value: JsonObject, states: list[str]) -> tuple[list[str], str]:
    from_states = [_constant(item) for item in _text_items(value.get("fromStates") or [], "action.fromStates", 8)]
    to_state = _constant(_required_text(value, "toState", 120))
    unknown = [state for state in [*from_states, to_state] if state not in states]
    if unknown:
        raise FdePlatformToolError(
            "schema_invalid", f"업무 버튼이 정의되지 않은 상태를 사용합니다: {', '.join(unknown)}"
        )
    return from_states, to_state


def _action_access(value: JsonObject, actor_roles: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    allowed_actors = _text_items(value.get("allowedActors") or [], "action.allowedActors", _MAX_ACTORS)
    roles_by_actor = {row["displayName"]: row["role"] for row in actor_roles}
    unknown_actors = sorted(set(allowed_actors) - roles_by_actor.keys())
    if unknown_actors:
        raise FdePlatformToolError(
            "schema_invalid", f"업무 버튼이 참여자로 정리되지 않은 사람을 참조합니다: {', '.join(unknown_actors)}"
        )
    return allowed_actors, [roles_by_actor[actor] for actor in allowed_actors]


def _action_parameters(information: list[str]) -> list[dict[str, object]]:
    return [
        {"apiName": _camel(item, f"input{parameter_index + 1}"), "displayName": item, "type": "string"}
        for parameter_index, item in enumerate(information)
    ]


def _readiness_gaps(
    actors: list[str],
    records: Sequence[JsonObject],
    states: list[str],
    actions: Sequence[JsonObject],
    policies: Sequence[JsonObject],
    evidence: list[str],
) -> list[dict[str, str]]:
    checks = (
        (actors, "actors", "이 업무를 수행하거나 영향을 받는 사람은 누구인가요?"),
        (records, "records", "업무에서 계속 추적해야 하는 기록은 무엇인가요?"),
        (len(states) >= 2, "lifecycleStates", "주요 기록이 거치는 상태를 시작부터 종료까지 적어주세요."),
        (actions, "actions", "사람이 누를 업무 버튼과 버튼 뒤에 바뀔 상태를 적어주세요."),
        (
            not actions or all(action.get("allowedActors") for action in actions),
            "actionPermissions",
            "각 업무 버튼을 누를 수 있는 사람을 참여자 중에서 정해주세요.",
        ),
        (policies, "policies", "반드시 지켜야 할 규칙이나 예외를 한 가지 이상 적어주세요."),
        (evidence, "evidence", "나중에 누가 무엇을 했는지 확인할 증거를 적어주세요."),
    )
    return [{"field": field, "question": question} for value, field, question in checks if not value]


def _readiness(gaps: list[dict[str, str]]) -> dict[str, object]:
    return {
        "isReady": not gaps,
        "status": "ready_for_review" if not gaps else "needs_more_detail",
        "missingCount": len(gaps),
        "questions": gaps,
    }


def _screens(primary_record: JsonObject | None, policies: Sequence[JsonObject]) -> list[dict[str, object]]:
    record_name = "업무" if primary_record is None else str(primary_record["displayName"])
    screens: list[dict[str, object]] = [
        {"id": "today", "name": "오늘 할 일", "job": "지금 처리해야 할 건을 우선순위대로 보여줍니다."},
        {
            "id": "record",
            "name": f"{record_name} 상세",
            "job": "현재 상태, 다음 행동, 필요한 정보를 한곳에서 보여줍니다.",
        },
        {"id": "evidence", "name": "변경 기록", "job": "누가 언제 무엇을 바꿨는지 확인합니다."},
    ]
    if policies:
        screens.insert(2, {"id": "policy", "name": "규칙 확인", "job": "차단·경고·사람 검토 규칙을 설명합니다."})
    return screens


def _object_resource(record: JsonObject, dataset_ref: str) -> dict[str, object]:
    fields = _mapping_items(record.get("fields"), "record.fields", _MAX_FIELDS + 3)
    definition = {
        "apiName": record["apiName"],
        "displayName": record["displayName"],
        "primaryKey": record["primaryKey"],
        "titleProperty": "name",
        "backing": {
            "dataset": dataset_ref,
            "mode": "snapshot",
            "primaryKeyColumns": [_snake(str(record["primaryKey"]))],
        },
        "properties": [_ontology_property(field) for field in fields],
    }
    return {"kind": "objectType", "definition": definition}


def _ontology_property(field: JsonObject) -> dict[str, object]:
    is_status = field["apiName"] == "status"
    return {
        "apiName": field["apiName"],
        "displayName": field["displayName"],
        "column": _snake(str(field["apiName"])),
        "type": field["type"],
        "nullable": field.get("required") is not True,
        "indexed": field["apiName"] in {"status"} or str(field["apiName"]).endswith("Id"),
        "editable": is_status,
    }


def _action_resource(action: JsonObject, policies: list[dict[str, object]]) -> dict[str, object]:
    params = [
        {"apiName": value["apiName"], "type": value["type"], "required": True}
        for value in _mapping_items(action.get("parameters"), "action.parameters", 12)
    ]
    definition: dict[str, object] = {
        "apiName": action["apiName"],
        "displayName": action["displayName"],
        "target": action["targetRecord"],
        "parameters": params,
        "permissions": {"allowedRoles": action["allowedRoles"]},
        "mutations": [{"type": "setProperty", "property": "status", "value": action["toState"]}],
    }
    preconditions: list[dict[str, object]] = []
    from_states = action.get("fromStates")
    if isinstance(from_states, list) and from_states:
        preconditions.append(
            {
                "safeExpression": f"object.status in {from_states!r}",
                "message": (
                    f"{action['displayName']}은(는) "
                    f"{', '.join(str(item) for item in from_states)} 상태에서만 가능합니다."
                ),
            }
        )
    preconditions.extend(policy_preconditions(action, policies))
    if preconditions:
        definition["preconditions"] = preconditions
    return {"kind": "actionType", "definition": definition}


def _workflow_actions(blueprint: JsonObject) -> list[dict[str, object]]:
    workflow = _mapping(blueprint.get("workflow"), "domainOsBlueprint.workflow")
    return _mapping_items(workflow.get("actions"), "domainOsBlueprint.workflow.actions", _MAX_ACTIONS)


def _workflow_states(blueprint: JsonObject) -> list[str]:
    workflow = _mapping(blueprint.get("workflow"), "domainOsBlueprint.workflow")
    return _text_items(workflow.get("states"), "domainOsBlueprint.workflow.states", _MAX_STATES)


def _sample_value(field: JsonObject, states: list[str]) -> object:
    if field["apiName"] == "status":
        return states[0] if states else "NEW"
    return {
        "integer": 0,
        "float": 0.0,
        "boolean": False,
        "date": "2026-01-01",
        "timestamp": "2026-01-01T00:00:00Z",
    }.get(str(field["type"]), f"Sample {field['displayName']}")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _mapping_items(value: object, field: str, limit: int) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list")
    if len(value) > limit or not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", f"{field} exceeds its bounded object-list contract")
    return [{str(name): item for name, item in row.items()} for row in value if isinstance(row, Mapping)]


def _text_items(value: object, field: str, limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list of text values")
    if len(value) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} exceeds its limit of {limit}")
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(items) != len(value):
        raise FdePlatformToolError("schema_invalid", f"{field} must contain non-empty text values")
    return items


def _required_text(value: JsonObject, field: str, limit: int) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip() or len(item) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be non-empty text up to {limit} characters")
    return item.strip()


def _optional_text(value: JsonObject, field: str, limit: int) -> str:
    item = value.get(field)
    if item is None:
        return ""
    if not isinstance(item, str) or len(item) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be text up to {limit} characters")
    return item.strip()


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9가-힣]+", value)


def _pascal(value: str, fallback: str) -> str:
    words = _words(value)
    ascii_words = [word for word in words if word.isascii()]
    result = "".join(word[:1].upper() + word[1:] for word in ascii_words)
    if not result or not result[0].isalpha():
        result = fallback
    return result[:64]


def _camel(value: str, fallback: str | None = None) -> str:
    pascal = _pascal(value, fallback or "value")
    return pascal[:1].lower() + pascal[1:]


def _optional_api_name(value: JsonObject, field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", item) is None:
        raise FdePlatformToolError("schema_invalid", f"{field} must be a stable English API name")
    return item


def _snake(value: str) -> str:
    separated = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"[^a-z0-9]+", "_", separated).strip("_")[:64]


def _constant(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9가-힣]+", "_", value).strip("_").upper()
    if not result:
        raise FdePlatformToolError("schema_invalid", "상태 이름에는 글자나 숫자가 필요합니다.")
    return result[:64]


def _actor_roles(application_name: str, actors: list[str]) -> list[dict[str, str]]:
    return [
        {
            "displayName": actor,
            "role": f"domain_actor_{hashlib.sha256(f'{application_name}:{actor}'.encode()).hexdigest()[:12]}",
        }
        for actor in actors
    ]


__all__ = [
    "application_resources",
    "build_domain_os_blueprint",
    "ontology_resources",
    "require_ready_blueprint",
    "seed_plan",
]
