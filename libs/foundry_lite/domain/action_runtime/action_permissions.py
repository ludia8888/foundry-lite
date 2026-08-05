"""Canonical role policy for Action Type view, edit, and apply operations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, cast

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

ActionPermissionOperation = Literal["view", "edit", "apply"]

_ROLE_FIELDS = ("viewRoles", "editRoles", "applyRoles", "allowedRoles")
_FIELD_BY_OPERATION: dict[ActionPermissionOperation, str] = {
    "view": "viewRoles",
    "edit": "editRoles",
    "apply": "applyRoles",
}


def compile_action_permissions(raw: object) -> Mapping[str, object]:
    """Validate and deterministically normalize an Action permission block.

    ``allowedRoles`` remains a read-compatible alias for ``applyRoles``. New
    definitions may declare the three operations independently.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationFailed("action permissions must be an object")
    permissions = dict(cast(Mapping[str, object], raw))
    for field in _ROLE_FIELDS:
        if field in permissions:
            permissions[field] = list(_roles(permissions[field], f"permissions.{field}"))
    if "applyRoles" not in permissions and "allowedRoles" in permissions:
        permissions["applyRoles"] = list(cast(Sequence[str], permissions["allowedRoles"]))
    return permissions


def action_permission_roles(
    permissions: Mapping[str, object], operation: ActionPermissionOperation
) -> tuple[str, ...] | None:
    """Return declared roles for one operation, or ``None`` for legacy fallback."""
    field = _FIELD_BY_OPERATION[operation]
    raw = permissions.get(field)
    if raw is None and operation == "apply":
        field = "allowedRoles"
        raw = permissions.get(field)
    if raw is None:
        return None
    return _roles(raw, f"permissions.{field}")


def can_access_action(
    ctx: RequestContext,
    permissions: Mapping[str, object],
    operation: ActionPermissionOperation,
) -> bool:
    """Evaluate the additional Action-specific role intersection."""
    roles = action_permission_roles(permissions, operation)
    if roles is None:
        return True
    return ctx.has_role("admin") or bool(set(ctx.roles) & set(roles))


def require_action_access(
    ctx: RequestContext,
    action_api_name: str,
    permissions: Mapping[str, object],
    operation: ActionPermissionOperation,
) -> None:
    """Reject a caller outside the Action-specific role grant."""
    if can_access_action(ctx, permissions, operation):
        return
    roles = action_permission_roles(permissions, operation) or ()
    raise PermissionDenied(
        f"permission denied to {operation} Action Type",
        details={
            "actionType": action_api_name,
            "operation": operation,
            "requiredRoles": sorted(set(roles) | {"admin"}),
        },
    )


def _roles(raw: object, field: str) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed(f"{field} must be a list of strings")
    roles: list[str] = []
    for index, value in enumerate(cast(Sequence[object], raw)):
        if not isinstance(value, str) or not value.strip():
            raise ValidationFailed(f"{field} entries must be non-empty strings", details={"index": index})
        roles.append(value.strip())
    return tuple(sorted(set(roles)))
