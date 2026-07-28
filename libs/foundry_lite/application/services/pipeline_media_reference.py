"""Authoritative immutable MediaVersion coordinates shared by Pipeline runtimes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import InvariantViolation, ValidationFailed

JsonObject = dict[str, object]


def required_source_media_reference(item: Mapping[str, object]) -> JsonObject:
    """Resolve the original committed MediaVersion without mixing derivative bytes."""

    for field in ("mediaReference", "sourceMediaReference"):
        nested = item.get(field)
        if isinstance(nested, Mapping):
            return validated_media_reference(nested)
    if item.get("mediaDerivativeId") is not None or item.get("derivativeKind") is not None:
        raise InvariantViolation("pipeline derivative lost its authoritative source media reference")
    return validated_media_reference(item)


def source_media_references_by_version(
    items: Sequence[Mapping[str, object]],
) -> dict[str, JsonObject]:
    """Index original MediaVersion references for derivative and Content Unit joins."""

    references: dict[str, JsonObject] = {}
    for item in items:
        reference = required_source_media_reference(item)
        references[str(reference["mediaItemVersionId"])] = reference
    return references


def attach_source_media_reference(
    item: Mapping[str, object],
    references: Mapping[str, JsonObject],
) -> JsonObject:
    """Attach the exact original reference to a Content Unit."""

    version_id = item.get("sourceMediaItemVersionId")
    reference = references.get(str(version_id)) if version_id else None
    if reference is None:
        raise InvariantViolation(
            "pipeline content unit lost its pinned source media reference",
            details={"sourceMediaItemVersionId": version_id},
        )
    locator = reference.get("sourceLocator")
    unit_locator = item.get("sourceLocator")
    combined_locator = {
        **(dict(locator) if isinstance(locator, Mapping) else {}),
        **(dict(unit_locator) if isinstance(unit_locator, Mapping) else {}),
    }
    return {
        **dict(item),
        "mediaReference": {**dict(reference), "sourceLocator": combined_locator},
    }


def validated_media_reference(value: Mapping[str, object]) -> JsonObject:
    """Validate the immutable coordinates required by every media reference."""

    fields = ("mediaItemVersionId", "mimeType", "contentHash")
    missing = [field for field in fields if not isinstance(value.get(field), str) or not value.get(field)]
    if missing:
        raise ValidationFailed(
            "pipeline media reference requires immutable source coordinates",
            details={"missingFields": missing},
        )
    locator = value.get("sourceLocator")
    return {
        "mediaItemVersionId": value["mediaItemVersionId"],
        "mimeType": value["mimeType"],
        "contentHash": value["contentHash"],
        "sourceLocator": dict(locator) if isinstance(locator, Mapping) else {},
    }
