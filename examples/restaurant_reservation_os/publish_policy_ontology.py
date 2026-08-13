"""Publish the policy-rich restaurant reservation ontology into an open branch."""

from __future__ import annotations

import argparse
import copy
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "X-Roles": "admin,ops_manager,data_engineer",
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo",
}


def _call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"{base_url}{path}", data=body, method=method, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(request) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc


def _edit_property(api_name: str, data_type: str, display_name: str, *, is_indexed: bool = False) -> dict[str, Any]:
    return {
        "apiName": api_name,
        "displayName": display_name,
        "type": data_type,
        "source": "edit_layer",
        "editable": True,
        "editPolicy": "edit_only",
        "nullable": True,
        "indexed": is_indexed,
        "searchable": False,
    }


def _add_properties(definition: dict[str, Any], object_api_name: str, specs: list[tuple[str, str, str, bool]]) -> None:
    object_type = next(item for item in definition["objectTypes"] if item["apiName"] == object_api_name)
    existing = {item["apiName"] for item in object_type["properties"]}
    object_type["properties"].extend(
        _edit_property(api_name, data_type, display_name, is_indexed=is_indexed)
        for api_name, data_type, display_name, is_indexed in specs
        if api_name not in existing
    )


def _parameter(api_name: str, data_type: str, **metadata: Any) -> dict[str, Any]:
    return {"apiName": api_name, "type": data_type, "required": True, **metadata}


def _quote_inputs() -> list[dict[str, Any]]:
    return [
        _parameter("restaurant", "object", objectType="Restaurant"),
        _parameter("table", "object", objectType="DiningTable"),
        _parameter("desiredAt", "string"),
        _parameter("requestedAt", "string"),
        _parameter("partySize", "integer"),
        _parameter("guestPhone", "string"),
        _parameter("seatingPreference", "string"),
    ]


def _create_inputs() -> list[dict[str, Any]]:
    return [
        _parameter("restaurant", "object", objectType="Restaurant"),
        _parameter("table", "object", objectType="DiningTable"),
        _parameter("reservationId", "string"),
        _parameter("guestName", "string"),
        _parameter("guestPhone", "string"),
        _parameter("guestEmail", "string"),
        _parameter("partySize", "integer"),
        _parameter("desiredAt", "string"),
        _parameter("requestedAt", "string"),
        _parameter("seatingPreference", "string"),
        _parameter("specialRequest", "string"),
        _parameter("customerAcceptedPolicy", "boolean"),
    ]


def _payment_inputs() -> list[dict[str, Any]]:
    return [
        _parameter("reservation", "object", objectType="Reservation"),
        _parameter("table", "object", objectType="DiningTable"),
        _parameter("paymentAttemptId", "string"),
        _parameter("paymentMethodRef", "string"),
        _parameter("requestedAt", "string"),
    ]


def _cancellation_inputs() -> list[dict[str, Any]]:
    return [
        _parameter("reservation", "object", objectType="Reservation"),
        _parameter("table", "object", objectType="DiningTable"),
        _parameter("cancellationId", "string"),
        _parameter("cancellationReason", "string"),
        _parameter("requestedAt", "string"),
    ]


def _function(api_name: str, display_name: str, entrypoint: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    source = Path(__file__).with_name("policy_functions.py").read_text(encoding="utf-8")
    is_edit_function = api_name in EDIT_FUNCTION_API_NAMES
    return {
        "apiName": api_name,
        "displayName": display_name,
        "version": FUNCTION_VERSIONS[api_name],
        "runtime": "python",
        "inputs": copy.deepcopy(inputs),
        "output": {"type": "ontology_edit_batch" if is_edit_function else "struct"},
        "timeoutSeconds": 10,
        "permissions": {"allowedRoles": ["admin", "ops_manager", "data_engineer"]},
        "definition": {"source": source, "entrypoint": entrypoint},
    }


def _configuration_action(
    api_name: str,
    display_name: str,
    target: str,
    parameters: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "apiName": api_name,
        "displayName": display_name,
        "contractVersion": 3,
        "target": target,
        "parameters": copy.deepcopy(parameters),
        "riskLevel": "medium",
        "agentExecutionPolicy": "approval_required",
        "permissions": {"allowedRoles": ["admin", "ops_manager", "data_engineer"]},
        "rules": [
            {
                "kind": "modifyObject",
                "ruleId": f"{api_name}-policy",
                "objectType": target,
                "target": {"kind": "parameter", "parameter": "__target__"},
                "assignments": [
                    {
                        "property": parameter["apiName"],
                        "value": {"kind": "parameter", "parameter": parameter["apiName"]},
                    }
                    for parameter in parameters
                ],
            }
        ],
    }


def _restaurant_parameters() -> list[dict[str, Any]]:
    return [_parameter(api_name, data_type) for api_name, data_type in RESTAURANT_PARAMETER_FIELDS]


def _table_parameters() -> list[dict[str, Any]]:
    return [_parameter(api_name, data_type) for api_name, data_type in TABLE_PARAMETER_FIELDS]


def _add_policy_resources(definition: dict[str, Any]) -> None:
    _add_properties(definition, "Restaurant", RESTAURANT_PROPERTIES)
    _add_properties(definition, "DiningTable", TABLE_PROPERTIES)
    _add_properties(definition, "Reservation", RESERVATION_PROPERTIES)
    _mark_property_classifications(definition)
    functions = {item["apiName"]: item for item in definition.get("functionTypes", [])}
    functions["QuoteAtomicReservationPolicy"] = _function(
        "QuoteAtomicReservationPolicy", "원자적 예약 정책 견적", "quote_reservation_policy", _quote_inputs()
    )
    functions["CreateAtomicReservation"] = _function(
        "CreateAtomicReservation",
        "원자적 정책 예약 생성",
        "create_policy_compliant_reservation",
        _create_inputs(),
    )
    functions["AuthorizeReservationDeposit"] = _function(
        "AuthorizeReservationDeposit",
        "예약 보증금 승인",
        "authorize_reservation_deposit",
        _payment_inputs(),
    )
    functions["CancelReservationWithRefund"] = _function(
        "CancelReservationWithRefund",
        "예약 취소와 환불 판정",
        "cancel_reservation",
        _cancellation_inputs(),
    )
    definition["functionTypes"] = list(functions.values())
    actions = {item["apiName"]: item for item in definition.get("actionTypes", [])}
    legacy_create = actions.get("CreateReservation")
    if legacy_create is not None:
        legacy_create["riskLevel"] = "high"
    actions["ConfigureRestaurantBookingPolicy"] = _configuration_action(
        "ConfigureRestaurantBookingPolicy", "식당 예약 정책 설정", "Restaurant", _restaurant_parameters()
    )
    actions["ConfigureDiningTablePolicy"] = _configuration_action(
        "ConfigureDiningTablePolicy", "테이블 운영 정책 설정", "DiningTable", _table_parameters()
    )
    actions["ReserveTableWithAtomicInventory"] = {
        "apiName": "ReserveTableWithAtomicInventory",
        "displayName": "원자적 좌석 원장 고객 예약",
        "contractVersion": 3,
        "target": "Restaurant",
        "parameters": copy.deepcopy(_create_inputs()),
        "riskLevel": "high",
        "agentExecutionPolicy": "approval_required",
        "permissions": {"allowedRoles": ["admin", "ops_manager", "data_engineer"]},
        "function": {
            "apiName": "CreateAtomicReservation",
            "version": FUNCTION_VERSIONS["CreateAtomicReservation"],
            "executionMode": "per_request",
        },
    }
    actions["PayReservationDeposit"] = _function_action(
        "PayReservationDeposit",
        "예약 보증금 결제",
        "Reservation",
        _payment_inputs(),
        "AuthorizeReservationDeposit",
    )
    actions["CancelCustomerReservation"] = _function_action(
        "CancelCustomerReservation",
        "예약 취소와 환불",
        "Reservation",
        _cancellation_inputs(),
        "CancelReservationWithRefund",
    )
    definition["actionTypes"] = list(actions.values())


def _function_action(
    api_name: str,
    display_name: str,
    target: str,
    parameters: list[dict[str, Any]],
    function_api_name: str,
) -> dict[str, Any]:
    return {
        "apiName": api_name,
        "displayName": display_name,
        "contractVersion": 3,
        "target": target,
        "parameters": copy.deepcopy(parameters),
        "riskLevel": "high",
        "agentExecutionPolicy": "approval_required",
        "permissions": {"allowedRoles": ["admin", "ops_manager", "data_engineer"]},
        "function": {
            "apiName": function_api_name,
            "version": FUNCTION_VERSIONS[function_api_name],
            "executionMode": "per_request",
        },
    }


def _mark_property_classifications(definition: dict[str, Any]) -> None:
    reservation_type = next(item for item in definition["objectTypes"] if item["apiName"] == "Reservation")
    for prop in reservation_type["properties"]:
        classification = RESERVATION_PROPERTY_CLASSIFICATIONS.get(prop["apiName"])
        if classification is not None:
            prop["classification"] = classification


RESTAURANT_PROPERTIES = [
    ("timeZone", "string", "시간대", False),
    ("serviceStartLocal", "string", "서비스 시작", False),
    ("lastSeatingLocal", "string", "마지막 착석", False),
    ("slotIntervalMinutes", "integer", "예약 간격", False),
    ("bookingWindowDays", "integer", "예약 오픈 기간", False),
    ("minAdvanceMinutes", "integer", "최소 사전 예약", False),
    ("closedWeekdaysCsv", "string", "정기 휴무", False),
    ("maxOnlinePartySize", "integer", "온라인 최대 인원", False),
    ("largePartyThreshold", "integer", "대규모 인원 기준", False),
    ("depositPerPersonKrw", "integer", "1인 보증금", False),
    ("cancellationCutoffHours", "integer", "무료 취소 마감", False),
    ("lateArrivalGraceMinutes", "integer", "지각 유예", False),
    ("quoteValidityMinutes", "integer", "견적 유효시간", False),
    ("depositHoldMinutes", "integer", "보증금 결제 홀드", False),
    ("autoConfirmPartySizeMax", "integer", "자동 확정 최대 인원", False),
    ("policyVersion", "string", "정책 버전", True),
    ("policyMessage", "string", "고객 정책 안내", False),
]

TABLE_PROPERTIES = [
    ("area", "string", "좌석 구역", True),
    ("minPartySize", "integer", "최소 인원", False),
    ("turnTimeMinutes", "integer", "이용시간", False),
    ("isAccessible", "boolean", "휠체어 접근", False),
    ("isHighChairCompatible", "boolean", "유아 의자", False),
    ("isCombinable", "boolean", "결합 가능", False),
    ("tableType", "string", "테이블 유형", True),
    ("reservationLedgerJson", "string", "서버 예약 원장", False),
    ("inventoryUpdatedAt", "string", "재고 갱신 시각", False),
    ("lastReservationId", "string", "최근 예약 번호", False),
]

RESERVATION_PROPERTIES = [
    ("guestEmail", "string", "고객 이메일", False),
    ("requestedAt", "string", "요청 시각", False),
    ("createdAt", "string", "생성 시각", False),
    ("seatingPreference", "string", "좌석 선호", False),
    ("specialRequest", "string", "특별 요청", False),
    ("policyVersion", "string", "정책 버전", True),
    ("policyDecision", "string", "정책 결정", True),
    ("quotedDepositKrw", "integer", "견적 보증금", False),
    ("cancellationDeadlineAt", "string", "무료 취소 마감", False),
    ("lateArrivalGraceMinutes", "integer", "지각 유예", False),
    ("quoteExpiresAt", "string", "견적 만료", False),
    ("inventoryHoldStatus", "string", "재고 홀드 상태", True),
    ("turnTimeMinutes", "integer", "이용시간", False),
    ("customerAcceptedPolicy", "boolean", "정책 동의", False),
    ("sourceChannel", "string", "예약 채널", True),
    ("holdExpiresAt", "string", "좌석 홀드 만료", False),
    ("inventoryVersion", "integer", "재고 원장 버전", False),
    ("paymentStatus", "string", "결제 상태", True),
    ("paymentProvider", "string", "결제 제공자", True),
    ("paymentAttemptId", "string", "결제 시도 번호", False),
    ("paymentReference", "string", "결제 승인 참조", False),
    ("paymentAuthorizedAt", "string", "결제 승인 시각", False),
    ("paidDepositKrw", "integer", "결제 보증금", False),
    ("refundStatus", "string", "환불 상태", True),
    ("refundReference", "string", "환불 참조", False),
    ("refundedAmountKrw", "integer", "환불 금액", False),
    ("refundedAt", "string", "환불 시각", False),
    ("cancellationId", "string", "취소 요청 번호", False),
    ("cancellationReason", "string", "취소 사유", False),
    ("cancelledAt", "string", "취소 시각", False),
]

FUNCTION_VERSIONS = {
    "QuoteAtomicReservationPolicy": "1.0.0",
    "CreateAtomicReservation": "1.0.0",
    "AuthorizeReservationDeposit": "1.0.0",
    "CancelReservationWithRefund": "1.0.0",
}

EDIT_FUNCTION_API_NAMES = frozenset(
    {"CreateAtomicReservation", "AuthorizeReservationDeposit", "CancelReservationWithRefund"}
)

RESERVATION_PROPERTY_CLASSIFICATIONS = {
    "guestName": "pii",
    "guestPhone": "pii",
    "guestEmail": "pii",
    "specialRequest": "pii",
    "paymentAttemptId": "finance",
    "paymentReference": "finance",
    "paidDepositKrw": "finance",
    "refundReference": "finance",
    "refundedAmountKrw": "finance",
}

RESTAURANT_PARAMETER_FIELDS = [
    ("timeZone", "string"),
    ("serviceStartLocal", "string"),
    ("lastSeatingLocal", "string"),
    ("slotIntervalMinutes", "integer"),
    ("bookingWindowDays", "integer"),
    ("minAdvanceMinutes", "integer"),
    ("closedWeekdaysCsv", "string"),
    ("maxOnlinePartySize", "integer"),
    ("largePartyThreshold", "integer"),
    ("depositPerPersonKrw", "integer"),
    ("cancellationCutoffHours", "integer"),
    ("lateArrivalGraceMinutes", "integer"),
    ("quoteValidityMinutes", "integer"),
    ("autoConfirmPartySizeMax", "integer"),
    ("policyVersion", "string"),
    ("policyMessage", "string"),
]

TABLE_PARAMETER_FIELDS = [
    ("area", "string"),
    ("minPartySize", "integer"),
    ("turnTimeMinutes", "integer"),
    ("isAccessible", "boolean"),
    ("isHighChairCompatible", "boolean"),
    ("isCombinable", "boolean"),
    ("tableType", "string"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--branch-id", required=True)
    args = parser.parse_args()
    branch = _call(args.base_url, "GET", f"/api/ontology/branches/{args.branch_id}")
    definition = json.loads(branch["yamlText"])
    _add_policy_resources(definition)
    yaml_text = json.dumps(definition, ensure_ascii=False, indent=2)
    validation = _call(args.base_url, "POST", "/api/ontology/validate", {"yaml": yaml_text})
    if validation.get("status") != "valid":
        raise RuntimeError(json.dumps(validation, ensure_ascii=False, indent=2))
    updated = _call(
        args.base_url,
        "POST",
        f"/api/ontology/branches/{args.branch_id}/update",
        {"yamlText": yaml_text, "expectedFingerprint": branch["contentFingerprint"]},
    )
    print(json.dumps({"branchId": updated["id"], "fingerprint": updated["contentFingerprint"]}, indent=2))


if __name__ == "__main__":
    main()
