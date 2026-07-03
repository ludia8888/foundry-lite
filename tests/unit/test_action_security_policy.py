"""Action execute RBAC comes from ontology-declared allowedRoles, not only a static map.

Regression guard for the enforcement gap where ``permissions.allowedRoles`` in
ontology YAML was persisted but never consulted: any action missing from the static
``permission_roles`` map silently collapsed to admin-only. The policy now resolves
``action:execute:<apiName>`` from the active ontology first and only falls back to
the static map (fail-closed to admin for unknown actions).
"""

from __future__ import annotations

import pytest
from foundry_lite.application.services.ontology_yaml import action_allowed_roles, action_type_definition
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService


class _FakeActionRoleProvider:
    """Hand-written fake returning per-action declared roles, like the active ontology would."""

    def __init__(self, roles_by_action: dict[str, tuple[str, ...] | None]) -> None:
        self.roles_by_action = roles_by_action
        self.calls: list[tuple[str, str]] = []

    def __call__(self, tenant_id: str, action_api_name: str) -> tuple[str, ...] | None:
        self.calls.append((tenant_id, action_api_name))
        return self.roles_by_action.get(action_api_name)


def _policy(roles_by_action: dict[str, tuple[str, ...] | None]) -> PolicyService:
    return PolicyService(action_role_provider=_FakeActionRoleProvider(roles_by_action))


def test_yaml_declared_action_roles_are_enforced_without_policy_code_change() -> None:
    # ExpediteOrder exists only in the ontology (not in the static map); its
    # allowedRoles must grant ops_manager and deny everyone else except admin.
    policy = _policy({"ExpediteOrder": ("ops_manager",)})

    assert policy.decide(RequestContext(roles=("ops_manager",)), "action:execute:ExpediteOrder").allowed is True
    assert policy.decide(RequestContext(roles=("admin",)), "action:execute:ExpediteOrder").allowed is True
    for role in ("viewer", "data_engineer", "finance", "connector_ingest"):
        assert policy.decide(RequestContext(roles=(role,)), "action:execute:ExpediteOrder").allowed is False


def test_static_map_action_still_works_when_ontology_declares_no_roles() -> None:
    # ApproveOrder regression: an action without YAML allowedRoles keeps its
    # hardcoded grants ({admin, ops_manager}) unchanged.
    policy = _policy({"ApproveOrder": None})

    assert policy.decide(RequestContext(roles=("ops_manager",)), "action:execute:ApproveOrder").allowed is True
    assert policy.decide(RequestContext(roles=("admin",)), "action:execute:ApproveOrder").allowed is True
    assert policy.decide(RequestContext(roles=("viewer",)), "action:execute:ApproveOrder").allowed is False
    assert policy.decide(RequestContext(roles=("data_engineer",)), "action:execute:ApproveOrder").allowed is False


def test_ontology_declared_roles_override_the_static_map() -> None:
    # If the active ontology narrows ApproveOrder to finance, the static map's
    # ops_manager grant must lose: YAML is the source of truth when declared.
    policy = _policy({"ApproveOrder": ("finance",)})

    assert policy.decide(RequestContext(roles=("finance",)), "action:execute:ApproveOrder").allowed is True
    assert policy.decide(RequestContext(roles=("ops_manager",)), "action:execute:ApproveOrder").allowed is False


def test_unknown_action_fails_closed_to_admin_only() -> None:
    policy = _policy({})
    denied = policy.decide(RequestContext(roles=("ops_manager", "data_engineer")), "action:execute:GhostAction")

    assert denied.allowed is False
    assert "admin" in denied.reason
    assert "fails closed" in denied.reason
    assert policy.decide(RequestContext(roles=("admin",)), "action:execute:GhostAction").allowed is True
    with pytest.raises(PermissionDenied) as excinfo:
        policy.require(RequestContext(roles=("viewer",)), "action:execute:GhostAction")
    assert excinfo.value.details["permission"] == "action:execute:GhostAction"


def test_empty_declared_allowed_roles_means_admin_only() -> None:
    policy = _policy({"LockedAction": ()})

    assert policy.decide(RequestContext(roles=("admin",)), "action:execute:LockedAction").allowed is True
    assert policy.decide(RequestContext(roles=("ops_manager",)), "action:execute:LockedAction").allowed is False


def test_unwired_action_role_provider_fails_closed_by_default() -> None:
    policy = PolicyService()

    with pytest.raises(PermissionDenied, match="action role provider is not configured"):
        policy.require(RequestContext(roles=("admin",)), "action:execute:ApproveOrder")


def test_unwired_provider_can_be_explicitly_allowed_for_narrow_unit_tests() -> None:
    policy = PolicyService(allow_unwired_action_role_provider=True)

    assert policy.decide(RequestContext(roles=("ops_manager",)), "action:execute:ApproveOrder").allowed is True
    assert policy.decide(RequestContext(roles=("viewer",)), "action:execute:ApproveOrder").allowed is False


def test_non_action_permissions_never_consult_the_action_role_provider() -> None:
    provider = _FakeActionRoleProvider({})
    policy = PolicyService(action_role_provider=provider)

    assert policy.decide(RequestContext(roles=("viewer",)), "object:read").allowed is True
    assert provider.calls == []


def test_action_allowed_roles_reads_persisted_definition() -> None:
    definition = action_type_definition(
        {
            "apiName": "ExpediteOrder",
            "target": "Order",
            "permissions": {"allowedRoles": ["ops_manager", "finance"]},
        }
    )

    assert action_allowed_roles(definition) == ("ops_manager", "finance")
    assert action_allowed_roles({"apiName": "NoPermissions", "target": "Order"}) is None
    assert action_allowed_roles({"permissions": {"requireApproval": True}}) is None


def test_yaml_rejects_malformed_allowed_roles_at_ingestion() -> None:
    with pytest.raises(ValidationFailed, match="permissions.allowedRoles must be a list of strings"):
        action_type_definition(
            {"apiName": "ExpediteOrder", "target": "Order", "permissions": {"allowedRoles": "ops_manager"}}
        )
    with pytest.raises(ValidationFailed, match="entries must be strings"):
        action_type_definition(
            {"apiName": "ExpediteOrder", "target": "Order", "permissions": {"allowedRoles": ["ops_manager", 1]}}
        )
