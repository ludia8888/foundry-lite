"""Direct specification tests for the query-shape and row-policy validators.

The strategy/specification gate requires the rule modules split out of
``query.py`` and ``ontology_validation.py`` to be exercised through their own
seams, not only through the services that call them.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.services.object_store.query_validation import validate_query_properties
from foundry_lite.application.services.ontology_row_policy_validation import validate_yaml_row_policies
from foundry_lite.domain.errors import ValidationFailed

_PROPERTIES = {"orderId", "status", "riskScore", "margin"}
_MASKED = {"margin"}


def test_validate_query_properties_accepts_declared_visible_shapes() -> None:
    validate_query_properties(
        {"and": [{"property": "status", "op": "eq", "value": "PENDING"}]},
        [{"property": "riskScore", "direction": "desc"}],
        _PROPERTIES,
        _MASKED,
    )


def test_validate_query_properties_rejects_unknown_filter_property() -> None:
    with pytest.raises(ValidationFailed):
        validate_query_properties(
            {"property": "nope", "op": "eq", "value": 1},
            [],
            _PROPERTIES,
            _MASKED,
        )


def test_validate_query_properties_rejects_masked_property_in_filter_and_order() -> None:
    """Masked values must not be probeable through filters or sort order."""
    with pytest.raises(ValidationFailed, match="masked property"):
        validate_query_properties({"property": "margin", "op": "gt", "value": 0}, [], _PROPERTIES, _MASKED)
    with pytest.raises(ValidationFailed, match="masked property"):
        validate_query_properties(None, [{"property": "margin", "direction": "asc"}], _PROPERTIES, _MASKED)


def _order_object(row_policies: list[dict[str, object]]) -> dict[str, object]:
    return {"apiName": "Order", "rowPolicies": row_policies}


_PROPERTY_DEFS: dict[str, dict[str, object]] = {
    "region": {"apiName": "region", "column": "region", "type": "string"},
    "operatorNote": {"apiName": "operatorNote", "type": "string", "source": "edit_layer"},
}


def test_validate_yaml_row_policies_accepts_dataset_backed_filter() -> None:
    validate_yaml_row_policies(
        _order_object([{"role": "ops_manager", "filter": {"property": "region", "op": "eq", "value": "NA"}}]),
        _PROPERTY_DEFS,
    )


def test_validate_yaml_row_policies_rejects_unknown_property() -> None:
    with pytest.raises(ValidationFailed):
        validate_yaml_row_policies(
            _order_object([{"role": "ops_manager", "filter": {"property": "nope", "op": "eq", "value": 1}}]),
            _PROPERTY_DEFS,
        )


def test_validate_yaml_row_policies_rejects_edit_layer_property() -> None:
    """Edits would silently move rows across visibility; policies stay dataset-backed."""
    with pytest.raises(ValidationFailed):
        validate_yaml_row_policies(
            _order_object([{"role": "ops_manager", "filter": {"property": "operatorNote", "op": "eq", "value": "x"}}]),
            _PROPERTY_DEFS,
        )
