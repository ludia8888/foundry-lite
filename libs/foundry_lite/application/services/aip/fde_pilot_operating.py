"""Read-only release readiness for one AI FDE hosted business application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import OntologyCatalogResult, OsdkApplicationBundle
from foundry_lite.application.services.aip.fde_business_system_definition import (
    require_business_system_definition,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied

JsonObject = Mapping[str, object]


class OntologyReleaseReader(Protocol):
    def release_active_catalog(self, *, ctx: RequestContext | None = None) -> OntologyCatalogResult: ...


def assigned_roles(resources: Sequence[JsonObject], actor_user_id: str) -> set[str]:
    for resource in resources:
        metadata = _mapping(resource.get("metadata"))
        if metadata.get("principalId") == actor_user_id:
            return _text_set(metadata.get("roles"))
    raise PermissionDenied("이 업무 앱에 배정된 역할이 없습니다.")


def operating_application_view(
    definition_value: object,
    catalog: OntologyCatalogResult,
    application: OsdkApplicationBundle,
    *,
    has_role_mapping: bool,
) -> dict[str, object]:
    """Project live readiness from immutable definitions and current governed state."""

    definition = require_business_system_definition(definition_value)
    expected = _expected_resources(definition)
    missing_ontology = _missing_ontology_resources(catalog, expected)
    missing_grants = _missing_application_grants(application, expected)
    blockers = _blockers(application, missing_ontology, missing_grants, has_role_mapping=has_role_mapping)
    application_id = str(application["application"]["id"])
    return {
        "status": "operating" if not blockers else "awaiting_release",
        "operatingPath": f"/apps/{application_id}",
        "definitionFingerprint": definition["definitionFingerprint"],
        "ontologyVersionId": catalog["ontologyVersionId"],
        "ontologyVersionNumber": catalog["versionNumber"],
        "applicationId": application_id,
        "sameDefinitionOnEverySurface": True,
        "rollbackPolicy": "follow_active_ontology",
        "blockers": blockers,
    }


def require_operating_resource(definition_value: object, resource_kind: str, api_name: str) -> None:
    definition = require_business_system_definition(definition_value)
    expected = _expected_resources(definition)
    if api_name not in expected.get(resource_kind, set()):
        raise PermissionDenied(
            "업무 앱에 허용되지 않은 데이터 또는 Action입니다.",
            details={"resourceKind": resource_kind, "apiName": api_name},
        )


def require_operating_application(operating: JsonObject) -> None:
    if operating.get("status") != "operating":
        raise PermissionDenied("사람 승인이 끝나지 않아 업무 앱을 사용할 수 없습니다.")


def active_application_coordinates(application: OsdkApplicationBundle) -> tuple[str, tuple[str, ...]]:
    client = next((row for row in application["clients"] if row["status"] == "active"), None)
    if client is None:
        raise PermissionDenied("업무 앱의 로그인 클라이언트가 활성 상태가 아닙니다.")
    scopes = tuple(sorted({scope for row in application["resources"] for scope in row["scopes"]}))
    return str(client["client_id"]), scopes


def is_role_mapping(resource: JsonObject, application_id: str) -> bool:
    metadata = resource.get("metadata")
    return (
        resource.get("resourceType") == "business_application_role_mapping"
        and isinstance(metadata, Mapping)
        and metadata.get("applicationId") == application_id
    )


def _expected_resources(definition: JsonObject) -> dict[str, set[str]]:
    work = _mapping(definition.get("agentWork"))
    return {
        "object": _text_set(work.get("objectTypes")),
        "action": _action_names(work.get("actionTypes")),
        "function": _text_set(work.get("functionTypes")),
    }


def _missing_ontology_resources(
    catalog: OntologyCatalogResult,
    expected: Mapping[str, set[str]],
) -> dict[str, list[str]]:
    object_names = {str(item["apiName"]) for item in catalog["objectTypes"]}
    action_names = {
        str(action["apiName"]) for item in catalog["objectTypes"] for action in item["actions"] if action["enabled"]
    }
    function_names = {str(item["apiName"]) for item in catalog["functionTypes"]}
    actual = {"object": object_names, "action": action_names, "function": function_names}
    return {kind: sorted(names - actual[kind]) for kind, names in expected.items()}


def _missing_application_grants(
    application: OsdkApplicationBundle,
    expected: Mapping[str, set[str]],
) -> dict[str, list[str]]:
    grants = {(str(row["resource_type"]), str(row["resource_api_name"])) for row in application["resources"]}
    return {kind: sorted(name for name in names if (kind, name) not in grants) for kind, names in expected.items()}


def _blockers(
    application: OsdkApplicationBundle,
    missing_ontology: Mapping[str, list[str]],
    missing_grants: Mapping[str, list[str]],
    *,
    has_role_mapping: bool,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if application["application"]["status"] != "active":
        result.append(_blocker("application_inactive", "업무 앱의 로그인·권한 경계가 활성 상태가 아닙니다."))
    if not any(client["status"] == "active" for client in application["clients"]):
        result.append(_blocker("application_client_inactive", "업무 앱에 사용할 수 있는 로그인 클라이언트가 없습니다."))
    if any(missing_ontology.values()):
        result.append(_blocker("ontology_not_activated", "사람이 승인한 업무 구조가 아직 운영 Ontology에 없습니다."))
    if any(missing_grants.values()):
        result.append(_blocker("application_scope_incomplete", "업무 앱에 허용된 데이터와 Action 범위가 불완전합니다."))
    if not has_role_mapping:
        result.append(_blocker("role_mapping_missing", "업무 앱을 사용할 사람과 역할이 아직 연결되지 않았습니다."))
    return result


def _blocker(code: str, message: str) -> dict[str, object]:
    return {"code": code, "message": message}


def _action_names(value: object) -> set[str]:
    return {
        str(item["actionType"])
        for item in _mapping_items(value)
        if isinstance(item.get("actionType"), str) and item["actionType"]
    }


def _mapping(value: object) -> dict[str, object]:
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _mapping_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _text_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return set()
    return {item for item in value if isinstance(item, str) and item}


__all__ = [
    "OntologyReleaseReader",
    "active_application_coordinates",
    "assigned_roles",
    "is_role_mapping",
    "operating_application_view",
    "require_operating_application",
    "require_operating_resource",
]
