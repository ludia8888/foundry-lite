from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TypedDict

from foundry_lite.application.ports import ObjectQueryItem, ObjectSetDefinition, ObjectSetRow
from foundry_lite.domain.errors import ValidationFailed

OBJECT_SET_VISIBILITIES = {"private", "public", "temporary", "permanent"}
OBJECT_SET_ACCESS_SCOPES = {"private", "public"}
OBJECT_SET_LIFECYCLES = {"temporary", "permanent"}
PUBLIC_OBJECT_SET_VISIBILITIES = {"public", "permanent"}
PRIVATE_OBJECT_SET_VISIBILITIES = {"private", "temporary"}


class NormalizedObjectSetDefinition(TypedDict):
    definition: ObjectSetDefinition
    visibility: str
    expires_at: str | None


ObjectSetMembers = tuple[list[str], list[ObjectQueryItem]]


def object_set_definition_from_inputs(
    set_type: str,
    definition: Mapping[str, object] | None,
    object_ids: list[str] | None,
    filter_ast: Mapping[str, object] | None,
) -> ObjectSetDefinition:
    if definition is not None:
        normalized: ObjectSetDefinition = dict(definition)
        if set_type == "static" and set(normalized) == {"ids"}:
            return normalized
        if set_type == "dynamic" and set(normalized) == {"filter"}:
            return normalized
        raise ValidationFailed(
            "object set definition does not match set type",
            details={"set_type": set_type, "definition": normalized},
        )
    if set_type == "static":
        ids = object_ids or []
        return {"ids": ids}
    return {"filter": dict(filter_ast or {})}


def object_set_storage_visibility(
    *,
    visibility: str | None,
    access_scope: str | None,
    lifecycle: str | None,
    ttl_seconds: int | None,
) -> str:
    resolved_access_scope = resolve_object_set_access_scope(visibility, access_scope)
    resolve_object_set_lifecycle(visibility, lifecycle, ttl_seconds)
    return visibility or resolved_access_scope


def resolve_object_set_access_scope(visibility: str | None, access_scope: str | None) -> str:
    _require_valid_visibility(visibility)
    _require_valid_access_scope(access_scope)
    legacy_access_scope = object_set_access_scope(visibility) if visibility is not None else None
    _require_compatible_access_scope(visibility, access_scope, legacy_access_scope)
    return access_scope or legacy_access_scope or "private"


def resolve_object_set_lifecycle(visibility: str | None, lifecycle: str | None, ttl_seconds: int | None) -> str:
    if lifecycle is not None and lifecycle not in OBJECT_SET_LIFECYCLES:
        raise ValidationFailed("unsupported object set lifecycle", details={"lifecycle": lifecycle})
    legacy_lifecycle = legacy_object_set_lifecycle(visibility)
    if legacy_lifecycle is not None and lifecycle is not None and lifecycle != legacy_lifecycle:
        raise ValidationFailed(
            "object set visibility conflicts with lifecycle",
            details={"visibility": visibility, "lifecycle": lifecycle},
        )
    resolved = lifecycle or legacy_lifecycle or ("temporary" if ttl_seconds is not None else "permanent")
    _require_lifecycle_ttl_compatibility(resolved, ttl_seconds)
    return resolved


def object_set_access_scope(visibility: str) -> str:
    if visibility in PUBLIC_OBJECT_SET_VISIBILITIES:
        return "public"
    if visibility in PRIVATE_OBJECT_SET_VISIBILITIES:
        return "private"
    raise ValidationFailed("unsupported object set visibility", details={"visibility": visibility})


def object_set_lifecycle(row: ObjectSetRow) -> str:
    if row["expires_at"] is not None:
        return "temporary"
    return legacy_object_set_lifecycle(row["visibility"]) or "permanent"


def object_set_is_expired(row: ObjectSetRow) -> bool:
    expires_at = row["expires_at"]
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at) <= datetime.now().astimezone()


def object_set_expires_at(ttl_seconds: int | None) -> str | None:
    if ttl_seconds is None:
        return None
    return (datetime.now().astimezone() + timedelta(seconds=ttl_seconds)).isoformat()


def legacy_object_set_lifecycle(visibility: str | None) -> str | None:
    if visibility in OBJECT_SET_LIFECYCLES:
        return visibility
    return None


def _require_lifecycle_ttl_compatibility(lifecycle: str, ttl_seconds: int | None) -> None:
    if lifecycle == "temporary" and ttl_seconds is None:
        raise ValidationFailed("temporary object set lifecycle requires ttl_seconds")
    if lifecycle == "permanent" and ttl_seconds is not None:
        raise ValidationFailed("permanent object set lifecycle cannot define ttl_seconds")


def _require_valid_visibility(visibility: str | None) -> None:
    if visibility is not None and visibility not in OBJECT_SET_VISIBILITIES:
        raise ValidationFailed("unsupported object set visibility", details={"visibility": visibility})


def _require_valid_access_scope(access_scope: str | None) -> None:
    if access_scope is not None and access_scope not in OBJECT_SET_ACCESS_SCOPES:
        raise ValidationFailed("unsupported object set access_scope", details={"access_scope": access_scope})


def _require_compatible_access_scope(
    visibility: str | None,
    access_scope: str | None,
    legacy_access_scope: str | None,
) -> None:
    if legacy_access_scope is not None and access_scope is not None and access_scope != legacy_access_scope:
        raise ValidationFailed(
            "object set visibility conflicts with access_scope",
            details={"visibility": visibility, "access_scope": access_scope},
        )
