from __future__ import annotations

import pytest
from foundry_lite.domain.action_runtime.action_parameter_constraints import (
    validate_action_parameter_constraints,
    validate_action_parameter_constraints_for_value,
)
from foundry_lite.domain.action_runtime.action_parameter_types import matches_action_parameter_shape
from foundry_lite.domain.errors import ValidationFailed


def test_decimal_enum_identity_is_exact_and_rejects_semantic_duplicates() -> None:
    with pytest.raises(ValidationFailed, match="duplicate"):
        validate_action_parameter_constraints("decimal", {"enum": ["1", "1.0"]})

    validate_action_parameter_constraints_for_value("amount", "decimal", "1.000", {"enum": ["1"]})


def test_struct_shape_applies_nested_type_constraints_and_rejects_extra_fields() -> None:
    metadata = {
        "fields": [
            {
                "apiName": "amount",
                "type": "decimal",
                "required": True,
                "constraints": {"minimum": "1.0"},
            }
        ]
    }

    assert matches_action_parameter_shape("struct", metadata, {"amount": "1.00"})
    assert not matches_action_parameter_shape("struct", metadata, {"amount": "0.99"})
    assert not matches_action_parameter_shape("struct", metadata, {"amount": "1.00", "extra": True})


def test_struct_constraint_mapping_with_non_string_key_fails_closed() -> None:
    metadata = {
        "fields": [
            {
                "apiName": "amount",
                "type": "decimal",
                "required": True,
                "constraints": {1: "invalid"},
            }
        ]
    }

    assert not matches_action_parameter_shape("struct", metadata, {"amount": "1.00"})
