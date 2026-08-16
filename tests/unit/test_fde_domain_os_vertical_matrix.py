"""Cross-vertical proof that the Domain OS compiler stays domain-generic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from foundry_lite.application.services.aip.fde_domain_os_blueprint import (
    application_resources,
    build_domain_os_blueprint,
    ontology_resources,
    seed_plan,
)
from foundry_lite.application.services.aip.fde_pilot_osdk_bundle import consumer_osdk_plan, react_files
from foundry_lite.application.services.ontology_branch_diff import (
    ResourceKind,
    ResourceMap,
    parse_resource_map,
    serialize_resource_map,
)
from foundry_lite.application.services.ontology_validation import validate_ontology_definition
from foundry_lite.domain.context import demo_admin_context


def _record(name: str, api_name: str, *fields: tuple[str, str, str]) -> dict[str, object]:
    return {
        "name": name,
        "apiName": api_name,
        "fields": [
            {"name": label, "apiName": field_name, "type": field_type, "required": True}
            for label, field_name, field_type in fields
        ],
    }


def _action(name: str, api_name: str, from_states: list[str], to_state: str) -> dict[str, object]:
    return {
        "name": name,
        "apiName": api_name,
        "fromStates": from_states,
        "toState": to_state,
        "requiredInformation": ["처리 메모"],
    }


def _blocking_policy(
    name: str,
    statement: str,
    action_name: str,
    property_name: str,
    operator: str,
    value: object,
    evidence: str,
) -> dict[str, object]:
    return {
        "name": name,
        "statement": statement,
        "enforcement": "blocking",
        "appliesToActions": [action_name],
        "conditions": [{"propertyApiName": property_name, "operator": operator, "value": value}],
        "evidence": evidence,
    }


def _human_policy(name: str, statement: str, action_name: str, evidence: str) -> dict[str, object]:
    return {
        "name": name,
        "statement": statement,
        "enforcement": "manual_review",
        "appliesToActions": [action_name],
        "evidence": evidence,
    }


def _spec(
    identifier: str,
    application_name: str,
    description: str,
    actors: list[str],
    records: list[dict[str, object]],
    states: list[str],
    actions: list[dict[str, object]],
    policies: list[dict[str, object]],
    evidence: list[str],
) -> dict[str, object]:
    return {
        "id": identifier,
        "applicationName": application_name,
        "description": description,
        "brief": {
            "actors": actors,
            "records": records,
            "lifecycleStates": states,
            "actions": [{**action, "allowedActors": list(actors[1:] or actors)} for action in actions],
            "policies": policies,
            "evidence": evidence,
            "integrations": [],
            "successMeasures": ["누락된 다음 업무 0건", "모든 변경의 담당자와 시각 추적"],
        },
    }


VERTICAL_SPECS = (
    _spec(
        "restaurant-operations",
        "식당 예약 운영 OS",
        "예약 요청부터 좌석 배정, 보증금, 착석, 취소까지 같은 기록으로 운영합니다.",
        ["고객", "예약 담당자", "매니저"],
        [
            _record("예약", "Reservation", ("인원", "partySize", "integer"), ("보증금 결제", "depositPaid", "boolean")),
            _record("좌석", "DiningTable", ("수용 인원", "capacity", "integer")),
            _record("결제", "Payment", ("결제 금액", "amount", "float")),
        ],
        ["REQUESTED", "HELD", "CONFIRMED", "SEATED", "COMPLETED", "CANCELLED"],
        [
            _action("좌석 임시 확보", "HoldReservation", ["REQUESTED"], "HELD"),
            _action("예약 확정", "ConfirmReservation", ["HELD"], "CONFIRMED"),
            _action("착석 처리", "SeatParty", ["CONFIRMED"], "SEATED"),
            _action("이용 완료", "CompleteVisit", ["SEATED"], "COMPLETED"),
            _action("예약 취소", "CancelReservation", ["REQUESTED", "HELD", "CONFIRMED"], "CANCELLED"),
        ],
        [
            _blocking_policy(
                "보증금 확인",
                "예약 확정 전 보증금 결제가 필요합니다.",
                "ConfirmReservation",
                "depositPaid",
                "eq",
                True,
                "결제 승인 참조",
            ),
            _human_policy(
                "대규모 예약 확인",
                "대규모 예약의 좌석 구성은 매니저가 확인합니다.",
                "HoldReservation",
                "확인 담당자와 좌석 배치",
            ),
        ],
        ["예약 상태 전후", "좌석 배정", "결제·환불 참조"],
    ),
    _spec(
        "property-operations",
        "부동산 문의 계약 OS",
        "문의 접수부터 방문, 제안, 계약, 정산까지 고객과 매물을 함께 추적합니다.",
        ["고객", "중개 담당자", "계약 검토자"],
        [
            _record(
                "고객 문의",
                "PropertyInquiry",
                ("개인정보 동의", "privacyConsent", "boolean"),
                ("제안 금액", "offerAmount", "float"),
            ),
            _record("매물", "PropertyListing", ("주소", "address", "string")),
            _record("방문", "PropertyVisit", ("방문 일시", "visitAt", "timestamp")),
            _record("계약", "PropertyContract", ("계약 금액", "contractAmount", "float")),
        ],
        ["NEW", "QUALIFIED", "VISIT_SCHEDULED", "OFFERED", "CONTRACTED", "CLOSED"],
        [
            _action("문의 확인", "QualifyInquiry", ["NEW"], "QUALIFIED"),
            _action("방문 확정", "ScheduleVisit", ["QUALIFIED"], "VISIT_SCHEDULED"),
            _action("제안 제출", "SubmitOffer", ["VISIT_SCHEDULED"], "OFFERED"),
            _action("계약 체결", "SignContract", ["OFFERED"], "CONTRACTED"),
            _action("정산 완료", "CloseSettlement", ["CONTRACTED"], "CLOSED"),
        ],
        [
            _blocking_policy(
                "개인정보 동의",
                "고객 문의 처리 전 개인정보 동의가 필요합니다.",
                "QualifyInquiry",
                "privacyConsent",
                "eq",
                True,
                "동의 문구 버전과 시각",
            ),
            _human_policy(
                "계약 조건 검토",
                "계약 체결 전 권한 있는 담당자가 조건을 확인합니다.",
                "SignContract",
                "검토자와 계약서 버전",
            ),
        ],
        ["상담·방문 이력", "제안서 버전", "계약 검토자"],
    ),
    _spec(
        "tax-accounting",
        "세무회계 검토 OS",
        "증빙 수집, 전표, 대사 예외, 신고 검토와 제출 증거를 연결합니다.",
        ["경리 담당자", "세무 검토자", "승인권자"],
        [
            _record(
                "신고 건",
                "FilingCase",
                ("미해결 예외 수", "unresolvedExceptionCount", "integer"),
                ("검토자 확인", "reviewerSignoff", "boolean"),
            ),
            _record("증빙", "EvidenceDocument", ("문서 일자", "documentDate", "date")),
            _record("전표", "JournalEntry", ("금액", "amount", "float")),
            _record("대사 예외", "ReconciliationIssue", ("차이 금액", "differenceAmount", "float")),
        ],
        ["COLLECTING", "DRAFTED", "RECONCILING", "READY_FOR_REVIEW", "APPROVED", "FILED"],
        [
            _action("전표 초안 완료", "CompleteJournalDraft", ["COLLECTING"], "DRAFTED"),
            _action("대사 시작", "StartReconciliation", ["DRAFTED"], "RECONCILING"),
            _action("검토 요청", "SubmitFilingReview", ["RECONCILING"], "READY_FOR_REVIEW"),
            _action("신고 승인", "ApproveFiling", ["READY_FOR_REVIEW"], "APPROVED"),
            _action("제출 완료", "FileReturn", ["APPROVED"], "FILED"),
        ],
        [
            _blocking_policy(
                "대사 예외 해소",
                "검토 요청 전 미해결 예외가 없어야 합니다.",
                "SubmitFilingReview",
                "unresolvedExceptionCount",
                "eq",
                0,
                "예외 해소 내역",
            ),
            _human_policy(
                "신고 책임자 승인",
                "신고 제출 전 권한 있는 사람이 최종 내용을 확인합니다.",
                "ApproveFiling",
                "승인자와 신고서 해시",
            ),
        ],
        ["증빙 원본 참조", "전표 변경 이력", "승인자와 제출 영수증"],
    ),
    _spec(
        "lending-operations",
        "대출 심사 운영 OS",
        "서류 접수, 심사, 예외 검토, 사람 승인과 실행을 분리합니다.",
        ["신청자", "심사 담당자", "승인권자"],
        [
            _record(
                "대출 신청",
                "LoanApplication",
                ("서류 완비", "documentsComplete", "boolean"),
                ("부채상환비율", "debtToIncomeRatio", "float"),
            ),
            _record("신청자", "Borrower", ("본인 확인", "identityVerified", "boolean")),
            _record("제출 서류", "LoanDocument", ("문서 종류", "documentType", "string")),
            _record("심사 결정", "UnderwritingDecision", ("결정 사유", "decisionReason", "string")),
        ],
        ["RECEIVED", "DOCUMENT_CHECK", "UNDER_REVIEW", "EXCEPTION_REVIEW", "APPROVED", "DECLINED", "FUNDED"],
        [
            _action("서류 확인 시작", "StartDocumentCheck", ["RECEIVED"], "DOCUMENT_CHECK"),
            _action("심사 시작", "StartUnderwriting", ["DOCUMENT_CHECK"], "UNDER_REVIEW"),
            _action("예외 검토 요청", "RouteException", ["UNDER_REVIEW"], "EXCEPTION_REVIEW"),
            _action("대출 승인", "ApproveLoan", ["UNDER_REVIEW", "EXCEPTION_REVIEW"], "APPROVED"),
            _action("대출 거절", "DeclineLoan", ["UNDER_REVIEW", "EXCEPTION_REVIEW"], "DECLINED"),
            _action("실행 완료", "FundLoan", ["APPROVED"], "FUNDED"),
        ],
        [
            _blocking_policy(
                "서류 완비",
                "심사 시작 전 필수 서류가 모두 있어야 합니다.",
                "StartUnderwriting",
                "documentsComplete",
                "eq",
                True,
                "서류 체크리스트 버전",
            ),
            _human_policy(
                "사람의 최종 승인",
                "자동 계산만으로 승인하지 않고 권한 있는 사람이 확인합니다.",
                "ApproveLoan",
                "승인자와 판단 근거",
            ),
        ],
        ["신청 시점 자료", "모델·규칙 버전", "심사자 판단과 예외 사유"],
    ),
    _spec(
        "hospital-operations",
        "병원 접수 후속관리 OS",
        "접수, 예약, 동의 확인, 검사 완료와 후속 업무를 운영 기록으로 연결합니다.",
        ["환자", "접수 담당자", "의료진"],
        [
            _record(
                "진료 방문",
                "CareVisit",
                ("동의 기록", "consentRecorded", "boolean"),
                ("후속관리 필요", "followUpRequired", "boolean"),
            ),
            _record("환자", "Patient", ("본인 확인", "identityVerified", "boolean")),
            _record("예약", "Appointment", ("예약 일시", "appointmentAt", "timestamp")),
            _record("검사 지시", "TestOrder", ("검사 종류", "testType", "string")),
            _record("후속 업무", "FollowUpTask", ("기한", "dueAt", "timestamp")),
        ],
        ["REGISTERED", "SCHEDULED", "CHECKED_IN", "CONSENT_CONFIRMED", "TEST_COMPLETED", "FOLLOW_UP", "CLOSED"],
        [
            _action("예약 확정", "ScheduleCareVisit", ["REGISTERED"], "SCHEDULED"),
            _action("내원 확인", "CheckInPatient", ["SCHEDULED"], "CHECKED_IN"),
            _action("동의 확인", "ConfirmConsent", ["CHECKED_IN"], "CONSENT_CONFIRMED"),
            _action("검사 완료", "CompleteOrderedTest", ["CONSENT_CONFIRMED"], "TEST_COMPLETED"),
            _action("후속관리 시작", "StartFollowUp", ["TEST_COMPLETED"], "FOLLOW_UP"),
            _action("업무 종료", "CloseCareVisit", ["FOLLOW_UP"], "CLOSED"),
        ],
        [
            _blocking_policy(
                "동의 기록 확인",
                "검사 완료 처리 전 유효한 동의 기록이 필요합니다.",
                "CompleteOrderedTest",
                "consentRecorded",
                "eq",
                True,
                "동의서 버전과 확인 시각",
            ),
            _human_policy(
                "의료진 확인",
                "검사 완료와 후속관리 내용은 권한 있는 의료진이 확인합니다.",
                "CompleteOrderedTest",
                "확인자와 검사 결과 참조",
            ),
        ],
        ["본인 확인", "동의서 버전", "검사 결과와 후속 담당자"],
    ),
    _spec(
        "manufacturing-operations",
        "제조 생산 품질 OS",
        "주문, 생산, 품질검사, 출하와 클레임을 추적 가능한 상태로 운영합니다.",
        ["생산 관리자", "품질 검사자", "출하 담당자"],
        [
            _record(
                "생산 주문",
                "ProductionOrder",
                ("자재 출고", "materialsReleased", "boolean"),
                ("결함 수", "defectCount", "integer"),
            ),
            _record("품질 검사", "QualityCheck", ("검사 규격", "inspectionStandard", "string")),
            _record("출하", "Shipment", ("운송장", "trackingNumber", "string")),
            _record("클레임", "ManufacturingClaim", ("클레임 유형", "claimType", "string")),
        ],
        ["ORDERED", "RELEASED", "IN_PRODUCTION", "QUALITY_HOLD", "READY_TO_SHIP", "SHIPPED", "CLOSED"],
        [
            _action("생산 지시", "ReleaseProductionOrder", ["ORDERED"], "RELEASED"),
            _action("생산 시작", "StartProduction", ["RELEASED"], "IN_PRODUCTION"),
            _action("품질 검사 요청", "RequestQualityCheck", ["IN_PRODUCTION"], "QUALITY_HOLD"),
            _action("출하 승인", "ApproveShipment", ["QUALITY_HOLD"], "READY_TO_SHIP"),
            _action("출하 완료", "ShipOrder", ["READY_TO_SHIP"], "SHIPPED"),
            _action("주문 종료", "CloseProductionOrder", ["SHIPPED"], "CLOSED"),
        ],
        [
            _blocking_policy(
                "결함 0건 확인",
                "출하 승인 전 미해결 결함이 없어야 합니다.",
                "ApproveShipment",
                "defectCount",
                "eq",
                0,
                "검사 결과와 결함 조치",
            ),
            _human_policy(
                "품질 책임자 승인",
                "출하 전 품질 책임자가 검사 결과를 확인합니다.",
                "ApproveShipment",
                "품질 승인자와 규격 버전",
            ),
        ],
        ["작업 지시 버전", "검사 측정값", "출하 승인자와 운송장"],
    ),
)


@pytest.mark.parametrize("spec", VERTICAL_SPECS, ids=lambda value: str(value["id"]))
def test_vertical_brief_compiles_to_independent_objects_actions_policies_and_strict_osdk(
    spec: Mapping[str, object],
) -> None:
    arguments = {
        "applicationName": spec["applicationName"],
        "domainDescription": spec["description"],
        "domainBrief": spec["brief"],
    }
    blueprint = build_domain_os_blueprint(arguments)
    records = blueprint["records"]
    actions = blueprint["workflow"]["actions"]
    policies = blueprint["policies"]

    assert blueprint["readiness"]["isReady"] is True
    assert len(records) >= 3
    assert len(actions) >= 4
    assert {policy["automationStatus"] for policy in policies} >= {
        "executable_precondition",
        "human_confirmation",
    }
    assert all(policy["evidence"] for policy in policies)
    assert all(action["allowedActors"] for action in actions)
    assert all(action["allowedRoles"] for action in actions)
    assert blueprint["functions"] == []
    resources = ontology_resources(blueprint, f"seed.{spec['id']}")
    assert len(resources) == len(records) + len(actions)
    assert any(
        precondition.get("policyName")
        for resource in resources
        if resource["kind"] == "actionType"
        for precondition in resource["definition"].get("preconditions", [])
    )
    seeds = seed_plan(str(spec["id"]), blueprint)["datasets"]
    assert len(seeds) == len(records)
    assert len({seed["datasetRef"] for seed in seeds}) == len(records)
    assert len(application_resources(blueprint)) == len(records) + len(actions)
    resource_map: ResourceMap = {}
    for resource in resources:
        definition_row = cast(dict[str, object], resource["definition"])
        resource_map[(cast(ResourceKind, resource["kind"]), str(definition_row["apiName"]))] = definition_row
    definition = parse_resource_map(serialize_resource_map(resource_map))
    columns_by_dataset = {
        str(seed["datasetRef"]): {
            str(column): {"name": str(column), "nullable": column not in seed["primaryKey"]}
            for column in cast(list[dict[str, object]], seed["rows"])[0]
        }
        for seed in seeds
    }

    validate_ontology_definition(
        object(),
        demo_admin_context(),
        _definition_from_resource_map(definition),
        lambda _conn, _ctx, dataset_ref: columns_by_dataset[dataset_ref],
    )

    slug = str(spec["id"])
    plan = {
        **arguments,
        "domainOsBlueprint": blueprint,
        "consumerOsdk": consumer_osdk_plan(str(spec["applicationName"]), slug),
    }
    files = react_files(plan)
    package_name = f"@foundry-lite/{slug}-osdk"
    assert f'from "{package_name}/react"' in files["src/App.tsx"]
    assert "@foundry-lite/sdk" not in files["src/App.tsx"]
    assert "useFoundryLiteOsdkClient" not in files["src/App.tsx"]
    assert "screen.items.map" in files["src/App.tsx"]
    assert "업무 정보 자세히 보기" in files["src/App.tsx"]
    assert "JSON.stringify(item.properties" not in files["src/App.tsx"]
    assert "createBrowserFoundryLiteOsdkClient" in files["packages/application-osdk/src/react.ts"]
    assert all("@foundry-lite/sdk" not in content for name, content in files.items() if name.endswith((".ts", ".tsx")))


def _definition_from_resource_map(
    resources: Mapping[tuple[str, str], Mapping[str, object]],
) -> dict[str, object]:
    return {
        "objectTypes": [dict(resource) for (kind, _), resource in sorted(resources.items()) if kind == "objectType"],
        "linkTypes": [dict(resource) for (kind, _), resource in sorted(resources.items()) if kind == "linkType"],
        "actionTypes": [dict(resource) for (kind, _), resource in sorted(resources.items()) if kind == "actionType"],
        "functionTypes": [
            dict(resource) for (kind, _), resource in sorted(resources.items()) if kind == "functionType"
        ],
    }
