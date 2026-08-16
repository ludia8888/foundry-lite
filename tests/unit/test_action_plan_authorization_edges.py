from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports import ObjectTypeRow, PropertyTypeRow
from foundry_lite.application.services.action_plan_authorization import (
    authorize_action_edit_plan,
    inspect_action_edit_plan,
)
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.edit_plan import (
    EditPlan,
    LinkCreate,
    LinkDelete,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService

_OPS = RequestContext(tenant_id="tenant-a", actor_user_id="ops-1", roles=("ops_manager",))
_ADMIN = RequestContext(tenant_id="tenant-a", actor_user_id="admin-1", roles=("admin",))


def _contract(permissions: dict[str, object] | None = None):
    return compile_action_contract(
        {
            "apiName": "UpdateOrder",
            "contractVersion": 3,
            "target": "Order",
            "riskLevel": "high",
            "permissions": permissions or {"allowedRoles": ["ops_manager"]},
            "parameters": [],
            "rules": [],
        }
    )


def _property(name: str, *, is_editable: bool = True, classification: str | None = "public") -> PropertyTypeRow:
    return cast(
        PropertyTypeRow,
        {
            "id": f"property-{name}",
            "tenant_id": "tenant-a",
            "object_type_id": "type-order",
            "api_name": name,
            "display_name": name,
            "data_type": "string",
            "nullable": True,
            "indexed": False,
            "searchable": False,
            "editable": is_editable,
            "classification": classification,
            "source": "ontology",
            "column_name": name,
            "edit_policy": "direct",
            "derivation": None,
        },
    )


class _Ontology:
    def __init__(self, properties: list[PropertyTypeRow] | None = None) -> None:
        self.properties = properties or [
            _property("orderId", is_editable=False),
            _property("status"),
            _property("amount", classification="finance"),
            _property("locked", is_editable=False),
        ]

    def _active_object_type(self, *_args: object) -> ObjectTypeRow:
        return cast(
            ObjectTypeRow,
            {
                "id": "type-order",
                "tenant_id": "tenant-a",
                "ontology_version_id": "ontology-v1",
                "api_name": "Order",
                "display_name": "Order",
                "description": None,
                "primary_key_property": "orderId",
                "backing": {"kind": "dataset", "datasetRef": "raw.orders"},
                "config": {},
            },
        )

    def _properties_for_object_type(self, *_args: object) -> list[PropertyTypeRow]:
        return self.properties


def _plan(
    *,
    patch: dict[str, object] | None = None,
    create: dict[str, object] | None = None,
    should_delete: bool = False,
    link: str | None = None,
) -> EditPlan:
    return EditPlan(
        objects_to_create=(ObjectCreate("create", "create", "Order", "O-2", create),) if create else (),
        objects_to_modify=(ObjectModify("modify", "modify", "Order", "O-1", 1, patch),) if patch else (),
        objects_to_delete=(ObjectDelete("delete", "delete", "Order", "O-1", 1),) if should_delete else (),
        links_to_create=(LinkCreate("link", "link", link, "O-1", "C-1"),) if link else (),
        links_to_delete=(LinkDelete("unlink", "unlink", link, "O-1", "C-1"),) if link == "delete-link" else (),
    )


def test_action_plan_authorization_enforces_object_property_and_link_role_declarations() -> None:
    permissions = {
        "allowedRoles": ["ops_manager"],
        "objectTypeRoles": {"Order": ["ops_manager"]},
        "propertyRoles": {"Order.status": ["ops_manager"]},
        "linkTypeRoles": {"OrderCustomer": ["ops_manager"]},
    }
    plan = _plan(patch={"status": "APPROVED"}, link="OrderCustomer")
    sensitive = authorize_action_edit_plan(object(), _OPS, PolicyService(), _Ontology(), _contract(permissions), plan)

    assert sensitive == {"Order": frozenset({"amount"})}
    assert inspect_action_edit_plan(object(), _OPS, _Ontology(), _contract(permissions), plan) == sensitive


@pytest.mark.parametrize(
    "permissions",
    [
        {"objectTypeRoles": []},
        {"objectTypeRoles": {}},
        {"objectTypeRoles": {"Order": ["viewer"]}},
        {"objectTypeRoles": {"Order": ["ops_manager"]}, "propertyRoles": {}},
        {"objectTypeRoles": {"Order": ["ops_manager"]}, "propertyRoles": {"Order.status": ["viewer"]}},
        {"objectTypeRoles": {"Order": ["ops_manager"]}, "linkTypeRoles": {}},
    ],
)
def test_action_plan_resource_policy_fails_closed_for_invalid_missing_or_denied_entries(
    permissions: dict[str, object],
) -> None:
    plan = _plan(patch={"status": "APPROVED"}, link="OrderCustomer")
    with pytest.raises(PermissionDenied):
        authorize_action_edit_plan(object(), _OPS, PolicyService(), _Ontology(), _contract(permissions), plan)


@pytest.mark.parametrize(
    ("plan", "error", "match"),
    [
        (_plan(patch={"missing": "x"}), ValidationFailed, "unknown properties"),
        (_plan(patch={"orderId": "new-id"}), PermissionDenied, "primary key"),
        (_plan(patch={"locked": "x"}), PermissionDenied, "non-editable"),
    ],
)
def test_action_plan_schema_checks_reject_unknown_primary_key_and_readonly_edits(
    plan: EditPlan,
    error: type[Exception],
    match: str,
) -> None:
    with pytest.raises(error, match=match):
        inspect_action_edit_plan(object(), _ADMIN, _Ontology(), _contract(), plan)


def test_action_plan_sensitive_delete_and_link_permissions_are_independent() -> None:
    policy = PolicyService()
    with pytest.raises(PermissionDenied, match="object:edit:sensitive"):
        authorize_action_edit_plan(
            object(),
            RequestContext(tenant_id="tenant-a", roles=("data_engineer",)),
            policy,
            _Ontology(),
            _contract(),
            _plan(patch={"amount": "100"}),
        )
    with pytest.raises(PermissionDenied, match="object:delete"):
        authorize_action_edit_plan(
            object(),
            RequestContext(tenant_id="tenant-a", roles=("data_engineer",)),
            policy,
            _Ontology(),
            _contract(),
            _plan(should_delete=True),
        )
    with pytest.raises(PermissionDenied, match="link:edit"):
        authorize_action_edit_plan(
            object(),
            RequestContext(tenant_id="tenant-a", roles=("viewer",)),
            policy,
            _Ontology(),
            _contract(),
            _plan(link="OrderCustomer"),
        )

    deleted = _plan(should_delete=True)
    assert inspect_action_edit_plan(object(), _ADMIN, _Ontology(), _contract(), deleted) == {
        "Order": frozenset({"amount"})
    }


def test_action_plan_create_may_set_primary_key_and_readonly_property_but_still_requires_declarations() -> None:
    plan = _plan(create={"orderId": "O-2", "locked": "created-by-system"})
    permissions = {
        "objectTypeRoles": {"Order": ["ops_manager"]},
        "propertyRoles": {
            "Order.orderId": ["ops_manager"],
            "Order.locked": ["ops_manager"],
        },
    }

    assert authorize_action_edit_plan(object(), _OPS, PolicyService(), _Ontology(), _contract(permissions), plan) == {
        "Order": frozenset({"amount"})
    }
