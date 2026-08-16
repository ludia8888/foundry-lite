"""Fail-closed boundaries for natural-language Domain OS Functions."""

from __future__ import annotations

from copy import deepcopy

import pytest
from foundry_lite.application.services.aip.fde_domain_os_functions import (
    compile_domain_functions,
    function_application_resources,
    function_ontology_resource,
)
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError

_RECORDS = [
    {
        "apiName": "Trade",
        "fields": [
            {"apiName": "desk", "type": "string"},
            {"apiName": "notional", "type": "float"},
            {"apiName": "quantity", "type": "integer"},
            {"apiName": "isApproved", "type": "boolean"},
            {"apiName": "tradeDate", "type": "date"},
        ],
    }
]
_ACTOR_ROLES = [{"displayName": "risk reviewer", "role": "domain_actor_risk_reviewer"}]


def _function(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "승인 거래 수",
        "apiName": "CountApprovedTrades",
        "recordApiName": "Trade",
        "aggregation": "count",
        "allowedActors": ["risk reviewer"],
        "filters": [{"propertyApiName": "isApproved", "operator": "eq", "value": True}],
    }
    value.update(overrides)
    return value


def _compile(value: dict[str, object]) -> dict[str, object]:
    return compile_domain_functions({"functions": [value]}, _RECORDS, _ACTOR_ROLES)[0]


def test_boolean_and_list_filters_compile_to_valid_python_literals_and_execute_only_scope() -> None:
    value = _function(
        filters=[
            {"propertyApiName": "isApproved", "operator": "eq", "value": True},
            {"propertyApiName": "desk", "operator": "in", "value": ["KR", "US"]},
        ]
    )

    compiled = _compile(value)
    resource = function_ontology_resource(compiled)
    source = resource["definition"]["definition"]["source"]

    compile(source, "<generated-domain-function>", "exec")
    assert "{'$eq': True}" in source
    assert '{\'$in\': ["KR", "US"]}' in source
    assert function_application_resources([compiled]) == [
        {
            "resourceType": "function",
            "resourceApiName": "CountApprovedTrades",
            "scopes": ["osdk:function:CountApprovedTrades:execute"],
        }
    ]


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        ([{"propertyApiName": "quantity", "operator": "contains", "value": 1}], "포함 필터"),
        ([{"propertyApiName": "desk", "operator": "gt", "value": "Z"}], "크기 비교 필터"),
        ([{"propertyApiName": "desk", "operator": "eq", "value": 10}], "형식과 맞지 않습니다"),
        ([{"propertyApiName": "quantity", "operator": "in", "value": []}], "비어 있지 않은 목록"),
        ([{"propertyApiName": "quantity", "operator": "eq", "value": [1]}], "목록일 수 없습니다"),
        ([{"propertyApiName": "notional", "operator": "eq", "value": float("nan")}], "형식과 맞지 않습니다"),
        ([{"propertyApiName": "isApproved", "operator": "eq", "value": 1}], "형식과 맞지 않습니다"),
    ],
)
def test_function_filters_reject_operator_and_field_type_mismatches(
    filters: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(FdePlatformToolError, match=message):
        _compile(_function(filters=filters))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"recordApiName": "Missing"}, "없는 기록"),
        ({"aggregation": "median"}, "지원하지 않는 aggregation"),
        ({"aggregation": "sum", "propertyApiName": "desk", "filters": []}, "숫자 정보"),
        ({"aggregation": "sum", "filters": []}, "숫자 정보"),
        ({"filters": [{"propertyApiName": "missing", "operator": "eq", "value": 1}]}, "없는 정보"),
        ({"filters": [{"propertyApiName": "desk", "operator": "eq"}]}, "비교할 값"),
        ({"allowedActors": []}, "볼 수 있는 사용자"),
        ({"allowedActors": ["outsider"]}, "정의되지 않은 사용자"),
        ({"description": 7}, "description must be text"),
    ],
)
def test_function_contract_rejects_unresolved_or_malformed_domain_references(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(FdePlatformToolError, match=message):
        _compile(_function(**overrides))


def test_function_names_are_unique_and_korean_only_names_receive_stable_fallbacks() -> None:
    first = _function(apiName=None, name="승인 거래 수", filters=[])
    second = deepcopy(first)
    second["name"] = "거절 거래 수"
    compiled = compile_domain_functions({"functions": [first, second]}, _RECORDS, _ACTOR_ROLES)
    assert [row["apiName"] for row in compiled] == ["DomainFunction1", "DomainFunction2"]

    duplicate = [_function(filters=[]), _function(name="다른 표시 이름", filters=[])]
    with pytest.raises(FdePlatformToolError, match="계산 이름은 서로 달라야"):
        compile_domain_functions({"functions": duplicate}, _RECORDS, _ACTOR_ROLES)


@pytest.mark.parametrize("functions", ["not-a-list", [{}] * 13, ["not-an-object"]])
def test_function_list_is_bounded_and_object_only(functions: object) -> None:
    with pytest.raises(FdePlatformToolError, match="schema_invalid|must be a list|bounded object-list"):
        compile_domain_functions({"functions": functions}, _RECORDS, _ACTOR_ROLES)
