"""Cross-domain contract proof for non-developer Domain OS planning."""

from __future__ import annotations

from copy import deepcopy

import pytest
from foundry_lite.application.services.aip import fde_domain_os_policy
from foundry_lite.application.services.aip.fde_business_system_definition import (
    build_business_system_definition,
    require_business_system_definition,
)
from foundry_lite.application.services.aip.fde_domain_os_blueprint import (
    application_resources,
    build_domain_os_blueprint,
    ontology_resources,
    require_ready_blueprint,
    seed_plan,
)
from foundry_lite.application.services.aip.fde_domain_os_tool_schema import DOMAIN_BRIEF_SCHEMA
from foundry_lite.application.services.aip.fde_pilot_osdk_bundle import consumer_osdk_plan, react_files
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError
from foundry_lite.application.services.mcp_json_schema import McpJsonSchemaError, validate_mcp_json_schema
from foundry_lite.domain.errors import ValidationFailed


def test_property_maintenance_brief_compiles_independent_objects_actions_and_seed_data() -> None:
    blueprint = build_domain_os_blueprint(property_maintenance_arguments())

    assert blueprint["readiness"] == {
        "isReady": True,
        "status": "ready_for_review",
        "missingCount": 0,
        "questions": [],
    }
    assert blueprint["functions"] == []
    assert [record["apiName"] for record in blueprint["records"]] == ["WorkOrder", "PropertyAsset"]
    assert [action["apiName"] for action in blueprint["workflow"]["actions"]] == [
        "TriageWorkOrder",
        "ScheduleRepair",
        "CompleteRepair",
    ]
    assert blueprint["workflow"]["actions"][0]["parameters"] == [
        {"apiName": "priority", "displayName": "priority", "type": "string"}
    ]
    assert blueprint["workflow"]["actions"][0]["requiresApproval"] is True
    assert blueprint["workflow"]["actions"][0]["allowedActors"] == ["coordinator"]
    assert blueprint["workflow"]["actions"][0]["allowedRoles"][0].startswith("domain_actor_")
    assert [policy["automationStatus"] for policy in blueprint["policies"]] == [
        "human_confirmation",
        "executable_precondition",
    ]
    assert [screen["id"] for screen in blueprint["screens"]] == ["today", "record", "policy", "evidence"]

    resources = ontology_resources(blueprint, "seed.property_maintenance")
    assert len(resources) == 5
    assert resources[0]["definition"]["backing"]["dataset"] == "seed.property_maintenance"
    assert resources[1]["definition"]["backing"]["dataset"] == "seed.property_maintenance_property_asset"
    status_property = next(item for item in resources[0]["definition"]["properties"] if item["apiName"] == "status")
    assert status_property["editable"] is True
    assert status_property["editPolicy"] == "edit_wins"
    assert resources[2]["definition"]["preconditions"][0]["safeExpression"] == "object.status in ['REPORTED']"
    assert resources[2]["definition"]["riskLevel"] == "high"
    assert resources[2]["definition"]["agentExecutionPolicy"] == "approval_required"
    assert resources[4]["definition"]["riskLevel"] == "low"
    assert resources[4]["definition"]["agentExecutionPolicy"] == "autonomous"
    assert resources[2]["definition"]["permissions"] == {
        "allowedRoles": blueprint["workflow"]["actions"][0]["allowedRoles"]
    }
    assert resources[2]["definition"]["preconditions"][1] == {
        "op": "in",
        "left": {"kind": "objectProperty", "property": "severity"},
        "right": {"kind": "literal", "value": ["urgent", "normal"]},
        "message": "분류 전에 심각도가 urgent 또는 normal로 기록되어야 합니다.",
        "policyName": "심각도 필수",
    }

    scopes = application_resources(blueprint)
    assert scopes == [
        {
            "resourceType": "object",
            "resourceApiName": "WorkOrder",
            "scopes": ["osdk:object:WorkOrder:read"],
        },
        {
            "resourceType": "object",
            "resourceApiName": "PropertyAsset",
            "scopes": ["osdk:object:PropertyAsset:read"],
        },
        {
            "resourceType": "action",
            "resourceApiName": "TriageWorkOrder",
            "scopes": [
                "osdk:action:TriageWorkOrder:validate",
                "osdk:action:TriageWorkOrder:execute",
            ],
        },
        {
            "resourceType": "action",
            "resourceApiName": "ScheduleRepair",
            "scopes": [
                "osdk:action:ScheduleRepair:validate",
                "osdk:action:ScheduleRepair:execute",
            ],
        },
        {
            "resourceType": "action",
            "resourceApiName": "CompleteRepair",
            "scopes": [
                "osdk:action:CompleteRepair:validate",
                "osdk:action:CompleteRepair:execute",
            ],
        },
    ]

    seed = seed_plan("property_maintenance", blueprint)
    assert [item["datasetRef"] for item in seed["datasets"]] == [
        "seed.property_maintenance",
        "seed.property_maintenance_property_asset",
    ]
    assert seed["datasets"][0]["primaryKey"] == ["work_order_id"]
    assert seed["datasets"][1]["primaryKey"] == ["property_asset_id"]
    assert set(seed["datasets"][0]["rows"][0]) == {"work_order_id", "name", "status", "location", "severity"}


def test_business_system_definition_is_one_fingerprinted_contract_for_both_surfaces_and_gpt_work() -> None:
    arguments = property_maintenance_arguments()
    blueprint = build_domain_os_blueprint(arguments)
    consumer_osdk = consumer_osdk_plan("Property Care Desk", "property-care-desk")

    definition = build_business_system_definition("Property Care Desk", blueprint, consumer_osdk)

    assert definition["schemaVersion"] == "foundry-lite-business-system-definition/v2"
    assert definition["definitionFingerprint"].startswith("sha256:")
    experience = definition["experience"]
    workshop = experience["workshopApp"]
    assert experience["componentCatalogVersion"] == "foundry-lite-workshop-components/v4"
    assert workshop["theme"]["preset"] in {"ocean", "indigo", "emerald", "amber", "graphite"}
    assert workshop["theme"]["brandName"] == "Property Care Desk"
    assert workshop["shell"] == {
        "navigation": "sidebar",
        "density": "comfortable",
        "pageWidth": "wide",
        "showContextBar": True,
    }
    assert workshop["presentation"]["booleanLabels"] == {
        "trueLabel": "예",
        "falseLabel": "아니요",
        "emptyLabel": "정보 없음",
    }
    assert workshop["presentation"]["objectTypeNames"]["WorkOrder"] == "수리 요청"
    assert workshop["presentation"]["actionNames"]["TriageWorkOrder"] == "요청 분류"
    assert workshop["presentation"]["statusLabels"]["REPORTED"] == {
        "label": "접수됨",
        "intent": "info",
    }
    assert workshop["presentation"]["showTechnicalDetails"] is False
    page_ids = [page["pageId"] for page in workshop["pages"]]
    assert experience["surfaces"] == [
        {"id": "chatgpt", "pageIds": page_ids, "runtime": "workshop"},
        {"id": "external_app", "pageIds": page_ids, "runtime": "workshop"},
        {"id": "workshop", "pageIds": page_ids, "runtime": "workshop"},
    ]
    today = workshop["pages"][0]
    widgets = [widget for section in today["sections"] for widget in section["widgets"]]
    assert [widget["kind"] for widget in widgets] == [
        "objectSetTitle",
        "metricCard",
        "searchBar",
        "statusTracker",
        "filterList",
        "objectTable",
        "objectDetail",
        "buttonGroup",
    ]
    assert [section["span"] for section in today["sections"]] == [12, 8, 4]
    assert widgets[5]["config"]["objectApiName"] == "WorkOrder"
    assert set(widgets[7]["config"]["humanApprovalActionApiNames"]) == {
        "TriageWorkOrder",
        "ScheduleRepair",
    }
    assert definition["agentWork"]["actionTypes"][0]["execution"] == "proposal_then_human"
    assert require_business_system_definition(definition) == definition

    changed = deepcopy(definition)
    changed["experience"]["workshopApp"]["pages"][0]["name"] = "변조된 화면"
    with pytest.raises(FdePlatformToolError, match="정의서가 변경"):
        require_business_system_definition(changed)


def test_incomplete_brief_returns_plain_language_questions_and_cannot_generate() -> None:
    blueprint = build_domain_os_blueprint(
        {
            "applicationName": "시설 요청 OS",
            "domainDescription": "시설에서 발생한 요청을 빠짐없이 접수하고 처리합니다.",
            "domainBrief": {
                "actors": [],
                "records": [],
                "lifecycleStates": [],
                "actions": [],
                "policies": [],
                "evidence": [],
                "integrations": [],
                "successMeasures": [],
            },
        }
    )

    assert blueprint["readiness"]["isReady"] is False
    assert blueprint["readiness"]["missingCount"] == 6
    assert blueprint["readiness"]["questions"][0]["question"] == "이 업무를 수행하거나 영향을 받는 사람은 누구인가요?"
    with pytest.raises(FdePlatformToolError, match="업무 설계에 빈칸") as error:
        require_ready_blueprint(blueprint)
    assert error.value.reason == "domain_blueprint_incomplete"


def test_a_natural_language_aggregation_compiles_to_python_osdk_and_least_privilege_scope() -> None:
    arguments = property_maintenance_arguments()
    arguments["domainBrief"]["functions"] = [
        {
            "name": "긴급 요청 수",
            "apiName": "CountUrgentWorkOrders",
            "recordApiName": "WorkOrder",
            "aggregation": "count",
            "allowedActors": ["coordinator"],
            "filters": [{"propertyApiName": "severity", "operator": "eq", "value": "urgent"}],
        }
    ]

    blueprint = build_domain_os_blueprint(arguments)
    function = blueprint["functions"][0]
    assert function["apiName"] == "CountUrgentWorkOrders"
    assert function["allowedRoles"] == [blueprint["actorRoles"][1]["role"]]
    resource = ontology_resources(blueprint, "seed.property_maintenance")[-1]
    assert resource["kind"] == "functionType"
    assert resource["definition"]["runtime"] == "python"
    assert "FoundryClient().ontology.objects.WorkOrder" in resource["definition"]["definition"]["source"]
    assert "records.where(severity={'$eq': \"urgent\"})" in resource["definition"]["definition"]["source"]
    assert application_resources(blueprint)[-1] == {
        "resourceType": "function",
        "resourceApiName": "CountUrgentWorkOrders",
        "scopes": ["osdk:function:CountUrgentWorkOrders:execute"],
    }
    consumer_osdk = consumer_osdk_plan("Property Care Desk", "property-care-desk")
    files = react_files(
        {
            **arguments,
            "applicationName": "Property Care Desk",
            "domainOsBlueprint": blueprint,
            "consumerOsdk": consumer_osdk,
            "businessSystemDefinition": build_business_system_definition(
                "Property Care Desk", blueprint, consumer_osdk
            ),
        }
    )
    generated = files["packages/application-osdk/src/generated.ts"]
    assert "OsdkFunctionType<Record<string, never>, CountUrgentWorkOrdersOutput>" in generated
    assert "export const $Functions = { CountUrgentWorkOrders }" in generated
    assert '"functionApiNames": ["CountUrgentWorkOrders"]' in generated
    assert "/api/functions/" in files["packages/application-osdk/src/runtime.ts"]


def test_a_domain_function_cannot_sum_a_text_field_or_use_an_unknown_actor() -> None:
    text_metric = property_maintenance_arguments()
    text_metric["domainBrief"]["functions"] = [
        {
            "name": "지역 합계",
            "recordApiName": "WorkOrder",
            "aggregation": "sum",
            "propertyApiName": "location",
            "allowedActors": ["coordinator"],
        }
    ]
    with pytest.raises(FdePlatformToolError, match="숫자 정보"):
        build_domain_os_blueprint(text_metric)

    unknown_actor = property_maintenance_arguments()
    unknown_actor["domainBrief"]["functions"] = [
        {
            "name": "요청 수",
            "recordApiName": "WorkOrder",
            "aggregation": "count",
            "allowedActors": ["outsider"],
        }
    ]
    with pytest.raises(FdePlatformToolError, match="정의되지 않은 사용자"):
        build_domain_os_blueprint(unknown_actor)


def test_unknown_transition_and_duplicate_field_api_name_are_rejected() -> None:
    unknown_state = property_maintenance_arguments()
    unknown_state["domainBrief"]["actions"][0]["toState"] = "MISSING_STATE"
    with pytest.raises(FdePlatformToolError, match="정의되지 않은 상태"):
        build_domain_os_blueprint(unknown_state)

    duplicate_field = deepcopy(property_maintenance_arguments())
    duplicate_field["domainBrief"]["records"][0]["fields"].append(
        {"name": "Duplicate", "apiName": "location", "type": "string", "required": False}
    )
    with pytest.raises(FdePlatformToolError, match="정보 이름은 서로 달라야"):
        build_domain_os_blueprint(duplicate_field)

    unknown_actor = property_maintenance_arguments()
    unknown_actor["domainBrief"]["actions"][0]["allowedActors"] = ["outsider"]
    with pytest.raises(FdePlatformToolError, match="참여자로 정리되지 않은 사람"):
        build_domain_os_blueprint(unknown_actor)


def test_action_actor_permission_is_required_before_generation() -> None:
    arguments = property_maintenance_arguments()
    arguments["domainBrief"]["actions"][0]["allowedActors"] = []

    blueprint = build_domain_os_blueprint(arguments)

    assert blueprint["readiness"]["isReady"] is False
    assert blueprint["readiness"]["questions"] == [
        {
            "field": "actionPermissions",
            "question": "각 업무 버튼을 누를 수 있는 사람을 참여자 중에서 정해주세요.",
        }
    ]
    with pytest.raises(FdePlatformToolError, match="업무 설계에 빈칸"):
        require_ready_blueprint(blueprint)


@pytest.mark.parametrize(
    ("operator", "value", "message"),
    [
        ("lt", 3, "크기 비교 규칙"),
        ("eq", ["urgent"], "목록일 수 없습니다"),
        ("in", [], "비어 있지 않은 목록"),
        ("eq", 10, "업무 정보 형식과 맞지 않습니다"),
    ],
)
def test_policy_condition_rejects_operator_and_value_type_mismatches(
    operator: str,
    value: object,
    message: str,
) -> None:
    arguments = property_maintenance_arguments()
    condition = arguments["domainBrief"]["policies"][1]["conditions"][0]
    condition["operator"] = operator
    condition["value"] = value

    with pytest.raises(FdePlatformToolError, match=message):
        build_domain_os_blueprint(arguments)


@pytest.mark.parametrize(
    ("operator", "value"),
    [
        ("contains", "gent"),
        ("startsWith", "urg"),
        ("matches", "urgent|normal"),
    ],
)
def test_scalar_text_policy_operators_compile_to_executable_action_preconditions(
    operator: str,
    value: str,
) -> None:
    arguments = property_maintenance_arguments()
    condition = arguments["domainBrief"]["policies"][1]["conditions"][0]
    condition.update({"operator": operator, "value": value})

    blueprint = build_domain_os_blueprint(arguments)
    precondition = ontology_resources(blueprint, "seed.property_maintenance")[2]["definition"]["preconditions"][1]

    assert precondition["op"] == operator
    assert precondition["right"] == {"kind": "literal", "value": value}


def test_exists_policy_omits_value_from_input_schema_and_executable_precondition() -> None:
    arguments = property_maintenance_arguments()
    condition = arguments["domainBrief"]["policies"][1]["conditions"][0]
    condition.clear()
    condition.update({"propertyApiName": "severity", "operator": "exists"})

    validate_mcp_json_schema(arguments["domainBrief"], DOMAIN_BRIEF_SCHEMA)
    blueprint = build_domain_os_blueprint(arguments)
    precondition = ontology_resources(blueprint, "seed.property_maintenance")[2]["definition"]["preconditions"][1]

    assert precondition == {
        "op": "exists",
        "left": {"kind": "objectProperty", "property": "severity"},
        "message": "분류 전에 심각도가 urgent 또는 normal로 기록되어야 합니다.",
        "policyName": "심각도 필수",
    }


def test_policy_value_presence_contract_is_exact_for_exists_and_value_operators() -> None:
    exists_with_value = property_maintenance_arguments()
    condition = exists_with_value["domainBrief"]["policies"][1]["conditions"][0]
    condition.update({"operator": "exists", "value": None})
    with pytest.raises(McpJsonSchemaError, match="exactly one oneOf"):
        validate_mcp_json_schema(exists_with_value["domainBrief"], DOMAIN_BRIEF_SCHEMA)
    with pytest.raises(FdePlatformToolError, match="value 항목을 입력하지"):
        build_domain_os_blueprint(exists_with_value)

    equality_without_value = property_maintenance_arguments()
    equality_without_value["domainBrief"]["policies"][1]["conditions"][0].pop("value")
    with pytest.raises(McpJsonSchemaError, match="exactly one oneOf"):
        validate_mcp_json_schema(equality_without_value["domainBrief"], DOMAIN_BRIEF_SCHEMA)
    with pytest.raises(FdePlatformToolError, match="비교할 값이 필요"):
        build_domain_os_blueprint(equality_without_value)


@pytest.mark.parametrize(
    ("operator", "value", "message"),
    [
        ("contains", 1, "업무 정보 형식과 맞지"),
        ("matches", "(a+)+", "unsafe backtracking"),
        ("containsAny", ["urgent"], "지원하지 않는 규칙 비교 방식"),
    ],
)
def test_domain_policy_rejects_wrong_text_types_unsafe_regex_and_collection_only_operators(
    operator: str,
    value: object,
    message: str,
) -> None:
    arguments = property_maintenance_arguments()
    condition = arguments["domainBrief"]["policies"][1]["conditions"][0]
    condition.update({"operator": operator, "value": value})

    with pytest.raises(FdePlatformToolError, match=message):
        build_domain_os_blueprint(arguments)


def test_runtime_policy_validation_redacts_secret_bearing_condition_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_validation(_condition: object) -> None:
        raise ValidationFailed("provider token=super-secret")

    monkeypatch.setattr(fde_domain_os_policy, "validate_action_condition", fail_validation)

    with pytest.raises(FdePlatformToolError) as caught:
        fde_domain_os_policy._validate_runtime_condition("status", "eq", "APPROVED")

    assert "super-secret" not in str(caught.value)
    assert "***MASKED***" in str(caught.value)


def property_maintenance_arguments() -> dict[str, object]:
    return {
        "applicationName": "Property Care Desk",
        "domainDescription": "입주민의 시설 문제를 접수하고 담당자가 우선순위를 정한 뒤 수리 완료 증거까지 남깁니다.",
        "domainBrief": {
            "actors": ["resident", "coordinator", "vendor"],
            "records": [
                {
                    "name": "수리 요청",
                    "apiName": "WorkOrder",
                    "fields": [
                        {"name": "location", "apiName": "location", "type": "string", "required": True},
                        {"name": "severity", "apiName": "severity", "type": "string", "required": True},
                    ],
                },
                {
                    "name": "시설 자산",
                    "apiName": "PropertyAsset",
                    "fields": [
                        {"name": "serial number", "apiName": "serialNumber", "type": "string", "required": True}
                    ],
                },
            ],
            "lifecycleStates": ["REPORTED", "TRIAGED", "SCHEDULED", "COMPLETED"],
            "actions": [
                {
                    "name": "요청 분류",
                    "apiName": "TriageWorkOrder",
                    "fromStates": ["REPORTED"],
                    "toState": "TRIAGED",
                    "requiredInformation": ["priority"],
                    "allowedActors": ["coordinator"],
                },
                {
                    "name": "수리 일정 확정",
                    "apiName": "ScheduleRepair",
                    "fromStates": ["TRIAGED"],
                    "toState": "SCHEDULED",
                    "requiredInformation": ["visit window"],
                    "allowedActors": ["coordinator", "vendor"],
                    "requiresApproval": True,
                },
                {
                    "name": "수리 완료",
                    "apiName": "CompleteRepair",
                    "fromStates": ["SCHEDULED"],
                    "toState": "COMPLETED",
                    "requiredInformation": ["completion note"],
                    "allowedActors": ["vendor", "coordinator"],
                },
            ],
            "policies": [
                {
                    "name": "긴급 누수 우선 처리",
                    "statement": "긴급 누수는 일반 요청보다 먼저 배정해야 합니다.",
                    "enforcement": "manual_review",
                    "evidence": "분류 담당자와 판단 시각",
                    "appliesToActions": ["TriageWorkOrder"],
                },
                {
                    "name": "심각도 필수",
                    "statement": "분류 전에 심각도가 urgent 또는 normal로 기록되어야 합니다.",
                    "enforcement": "blocking",
                    "appliesToActions": ["TriageWorkOrder"],
                    "conditions": [{"propertyApiName": "severity", "operator": "in", "value": ["urgent", "normal"]}],
                },
            ],
            "evidence": ["상태 변경 전후", "담당자", "완료 사진"],
            "integrations": ["입주민 포털", "문자 알림"],
            "successMeasures": ["긴급 요청 15분 이내 분류", "미완료 누락 0건"],
        },
    }
