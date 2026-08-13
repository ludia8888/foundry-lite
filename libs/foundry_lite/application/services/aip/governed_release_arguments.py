"""Pure closed-input parsing for Governed Release tools."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]


def release_kind(arguments: JsonObject) -> str:
    kind = required_text(arguments, "releaseKind")
    if kind not in {"ontology", "pipeline"}:
        raise ValidationFailed("releaseKind must be ontology or pipeline", details={"releaseKind": kind})
    return kind


def require_pipeline(arguments: JsonObject) -> None:
    if release_kind(arguments) != "pipeline":
        raise ValidationFailed("deploy_release supports pipeline releases only")


def required_text(arguments: JsonObject, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required")
    return value.strip()


def optional_text(arguments: JsonObject, key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed(f"{key} must be a string")
    return value


def required_integer(arguments: JsonObject, key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationFailed(f"{key} must be a positive integer")
    return value


def mapping_items(payload: JsonObject) -> list[Mapping[str, object]]:
    value = payload.get("items")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


__all__ = [
    "mapping_items",
    "optional_text",
    "release_kind",
    "require_pipeline",
    "required_integer",
    "required_text",
]
