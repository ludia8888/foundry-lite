from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from foundry_lite.application.services.ontology_yaml import (
    _backing_datasource,
    action_allowed_roles,
    action_mutation,
    action_parameter,
    action_permissions,
    mapping_sequence,
    object_type_backing,
    optional_bool,
    optional_str,
    property_derivation,
    schema_columns,
    string_sequence,
    yaml_object,
    yaml_parse_error_details,
)
from foundry_lite.domain.errors import ValidationFailed


def test_yaml_shape_helpers_reject_ambiguous_python_values() -> None:
    assert mapping_sequence({"rows": None}, "rows") == ()
    assert optional_str({}, "name") is None
    assert optional_str({}, "name", "default") == "default"
    assert optional_bool({}, "required", True) is True

    failures = (
        lambda: yaml_object({1: "value"}, "root"),
        lambda: mapping_sequence({"rows": "not-a-list"}, "rows"),
        lambda: optional_str({"name": 1}, "name"),
        lambda: optional_bool({"required": "yes"}, "required", False),
        lambda: schema_columns({"columns": "not-a-list"}, "raw.orders"),
        lambda: string_sequence(["ok", 2], "roles"),
    )
    for failure in failures:
        with pytest.raises(ValidationFailed):
            failure()


def test_multi_datasource_backing_requires_unambiguous_nonempty_primary_keys_and_roles() -> None:
    with pytest.raises(ValidationFailed, match="at least one datasource"):
        object_type_backing({"backing": {"datasources": []}})
    with pytest.raises(ValidationFailed, match="exactly one column"):
        _backing_datasource(
            {"name": "orders", "dataset": "raw.orders", "primaryKeyColumns": ["id", "region"]},
            0,
        )
    with pytest.raises(ValidationFailed, match="non-empty string"):
        _backing_datasource(
            {"name": "orders", "dataset": "raw.orders", "requiredRole": ""},
            0,
        )

    backing = object_type_backing(
        {
            "backing": {
                "datasources": [
                    {
                        "name": "orders",
                        "dataset": "raw.orders",
                        "primaryKeyColumns": ["id"],
                        "requiredRole": "analyst",
                    }
                ],
                "mode": "snapshot",
            }
        }
    )
    assert backing["datasources"][0]["primaryKeyColumns"] == ("id",)
    assert backing["datasources"][0]["requiredRole"] == "analyst"


def test_derivation_action_and_schema_helpers_preserve_declared_fields() -> None:
    assert property_derivation({}) is None
    assert property_derivation({"derivation": None}) is None
    assert property_derivation({"derivation": {"expression": "price * quantity"}}) == {"expression": "price * quantity"}
    assert property_derivation({"derivation": {}}) == {}
    assert (
        schema_columns(
            {"columns": [{"name": "id", "type": "string"}, {"name": "total", "type": "double"}]},
            "raw.orders",
        )["total"]["type"]
        == "double"
    )

    assert action_permissions({}) is None
    assert action_permissions({"permissions": {"allowedRoles": ["admin"]}}) == {"allowedRoles": ["admin"]}
    assert action_allowed_roles({"permissions": {"allowedRoles": ["admin", "operator"]}}) == (
        "admin",
        "operator",
    )
    assert action_parameter({"apiName": "reason", "type": "string", "required": True})["required"] is True
    assert action_mutation({"type": "setProperty", "property": "status", "value": "approved"})["value"] == ("approved")
    assert (
        action_mutation({"type": "setProperty", "property": "status", "valueFrom": "newStatus"})["valueFrom"]
        == "newStatus"
    )


def test_yaml_parse_error_details_use_structured_location_then_safe_fallback() -> None:
    try:
        yaml.safe_load("root: [")
    except yaml.YAMLError as exc:
        details = yaml_parse_error_details(exc)
    else:
        raise AssertionError("broken YAML did not fail")

    assert details["line"] == 1
    assert details["column"] >= 1
    assert details["problem"]

    fallback = yaml_parse_error_details(
        yaml.YAMLError("plain failure"),
    )
    assert fallback == {"problem": "plain failure"}

    context_only = yaml_parse_error_details(
        SimpleNamespace(
            problem=None,
            problem_mark=None,
            context_mark=SimpleNamespace(line=2, column=4),
            context="while reading ontology",
        )
    )
    assert context_only == {
        "line": 3,
        "column": 5,
        "context": "while reading ontology",
    }
