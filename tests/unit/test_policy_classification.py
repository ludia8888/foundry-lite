"""Policy masking is driven by ontology classification, not hardcoded names.

This is the regression guard for the root fix behind the explain/preview/operations
leaks: sensitivity comes from each property's ontology ``classification`` (finance
/ pii), so a newly classified property in YAML is protected everywhere without any
policy code change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.security.policy import PolicyService

_ROWS: list[dict[str, object]] = [
    {
        "object_type_api_name": "Order",
        "property_api_name": "margin",
        "column_name": "margin",
        "classification": "finance",
    },
    {"object_type_api_name": "Customer", "property_api_name": "ssn", "column_name": "ssn_col", "classification": "pii"},
    {
        "object_type_api_name": "Order",
        "property_api_name": "status",
        "column_name": "status",
        "classification": "public",
    },
    {"object_type_api_name": "Order", "property_api_name": "note", "column_name": "note", "classification": None},
]


def _policy(rows: list[dict[str, object]] = _ROWS) -> PolicyService:
    def provider(tenant_id: str) -> Sequence[Mapping[str, object]]:
        del tenant_id
        return rows

    return PolicyService(classification_provider=provider)


def test_masking_covers_every_sensitive_classification_not_just_margin() -> None:
    policy = _policy()
    viewer = RequestContext(roles=("viewer",))
    # a pii property the policy never names by hand is protected automatically
    assert policy.masked_property_names(viewer, "Customer") == {"ssn"}
    # only sensitive classifications mask; public / unclassified stay visible
    assert policy.masked_property_names(viewer, "Order") == {"margin"}
    masked = policy.mask_properties(viewer, "Customer", {"ssn": "123-45-6789", "name": "Ada"})
    assert masked["ssn"] == "***MASKED***"
    assert masked["name"] == "Ada"


def test_sensitive_columns_derive_from_classified_backing_columns() -> None:
    policy = _policy()
    viewer = RequestContext(roles=("viewer",))
    assert policy.masked_column_names(viewer) == {"margin", "ssn_col"}
    rows = policy.mask_columns(viewer, [{"margin": 9.9, "ssn_col": "x", "status": "OPEN"}])
    assert rows[0]["margin"] == "***MASKED***"
    assert rows[0]["ssn_col"] == "***MASKED***"
    assert rows[0]["status"] == "OPEN"


def test_finance_and_admin_bypass_display_masking_but_audit_still_redacts() -> None:
    policy = _policy()
    for role in ("finance", "admin"):
        ctx = RequestContext(roles=(role,))
        assert policy.masked_property_names(ctx, "Order") == set()
        assert policy.masked_column_names(ctx) == set()
        # role-independent set (used by audit/operations redaction) still flags them
        assert policy.sensitive_column_names(ctx) == {"margin", "ssn_col"}
        assert policy.sensitive_property_names(ctx, "Customer") == {"ssn"}


def test_unwired_policy_fails_closed_by_default() -> None:
    policy = PolicyService()
    viewer = RequestContext(roles=("viewer",))

    with pytest.raises(PermissionDenied, match="classification provider is not configured"):
        policy.masked_property_names(viewer, "Order")


def test_unwired_policy_can_be_explicitly_allowed_for_narrow_unit_tests() -> None:
    policy = PolicyService(allow_unwired_classification_provider=True)
    viewer = RequestContext(roles=("viewer",))

    assert policy.masked_property_names(viewer, "Order") == set()
    assert policy.masked_column_names(viewer) == set()


def test_unknown_property_classification_fails_closed() -> None:
    policy = _policy(
        [
            {
                "object_type_api_name": "Order",
                "property_api_name": "margin",
                "column_name": "margin",
                "classification": "Finance",
            }
        ]
    )
    viewer = RequestContext(roles=("viewer",))

    with pytest.raises(PermissionDenied, match="unsupported property classification"):
        policy.masked_property_names(viewer, "Order")


def test_object_index_permission_is_operator_only() -> None:
    policy = PolicyService()

    assert policy.decide(RequestContext(roles=("data_engineer",)), "object:index").allowed is True
    assert policy.decide(RequestContext(roles=("ops_manager",)), "object:index").allowed is True
    assert policy.decide(RequestContext(roles=("viewer",)), "object:index").allowed is False
    assert policy.decide(RequestContext(roles=("finance",)), "object:index").allowed is False


def test_object_set_manage_permission_is_operator_only() -> None:
    policy = PolicyService()

    assert policy.decide(RequestContext(roles=("data_engineer",)), "object:set:manage").allowed is True
    assert policy.decide(RequestContext(roles=("ops_manager",)), "object:set:manage").allowed is True
    assert policy.decide(RequestContext(roles=("viewer",)), "object:set:manage").allowed is False
    assert policy.decide(RequestContext(roles=("finance",)), "object:set:manage").allowed is False
