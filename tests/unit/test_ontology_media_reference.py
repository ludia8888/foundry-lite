"""M4 ontology `media_reference` property-type validation (doc §1.6)."""

from __future__ import annotations

import pytest
from foundry_lite.application.services.ontology_validation import _validate_yaml_property_contracts
from foundry_lite.domain.errors import ValidationFailed


def _check(prop: dict[str, object]) -> None:
    _validate_yaml_property_contracts("Order", [prop])


def test_media_reference_property_with_valid_media_set_is_accepted() -> None:
    _check({"apiName": "sourceDocument", "type": "media_reference", "mediaSet": "legal.contracts"})


def test_media_reference_property_requires_a_media_set() -> None:
    with pytest.raises(ValidationFailed):
        _check({"apiName": "sourceDocument", "type": "media_reference"})


def test_media_reference_media_set_must_be_namespace_dot_name() -> None:
    with pytest.raises(ValidationFailed):
        _check({"apiName": "sourceDocument", "type": "media_reference", "mediaSet": "contracts"})


def test_media_reference_property_must_not_be_dataset_backed() -> None:
    with pytest.raises(ValidationFailed):
        _check(
            {
                "apiName": "sourceDocument",
                "type": "media_reference",
                "mediaSet": "legal.contracts",
                "source": "dataset",
                "column": "doc",
            }
        )


def test_unknown_property_type_is_still_rejected() -> None:
    with pytest.raises(ValidationFailed):
        _check({"apiName": "x", "type": "blob"})
