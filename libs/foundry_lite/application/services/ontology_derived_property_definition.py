"""Parser for the persisted shape of a link-derived property declaration."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.ontology_repository import PropertyDerivation
from foundry_lite.domain.errors import ValidationFailed


def property_derivation_from_value(value: object) -> PropertyDerivation:
    """Normalize a declared derivation without deciding whether it is valid."""
    if not isinstance(value, Mapping):
        raise ValidationFailed("derivation must be a mapping")
    result: PropertyDerivation = {}
    for field in ("expression", "link", "aggregation", "property"):
        _copy_optional_text(result, value, field)
    return result


def _copy_optional_text(result: PropertyDerivation, source: Mapping[object, object], field: str) -> None:
    value = source.get(field)
    if value is None:
        return
    if not isinstance(value, str):
        raise ValidationFailed(f"{field} must be a string")
    result[field] = value  # type: ignore[literal-required]
