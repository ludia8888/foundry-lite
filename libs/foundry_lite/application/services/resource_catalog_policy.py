"""Project-grant inheritance rules for the resource catalog."""

from __future__ import annotations

from typing import cast

from foundry_lite.application.ports.resource_catalog_repository import ProjectGrantRow, ProjectRole
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied

_ROLE_RANK: dict[str, int] = {
    "discoverer": 1,
    "viewer": 2,
    "editor": 3,
    "owner": 4,
}


def actor_project_role(ctx: RequestContext, grants: list[ProjectGrantRow]) -> ProjectRole | None:
    """Resolve the actor's inherited project role.

    v1 evaluates user and role principals. Group grants are stored for
    compatibility with the future resolver but ignored by the local resolver.
    """

    if ctx.has_role("admin"):
        return "owner"
    best: ProjectRole | None = None
    for grant in grants:
        matched = _grant_matches_actor(ctx, grant)
        if matched and _rank(grant["role"]) > _rank(best):
            best = cast(ProjectRole, grant["role"])
    return best


def require_project_role(role: ProjectRole | None, minimum_role: ProjectRole, *, project_id: str) -> ProjectRole:
    if _rank(role) < _rank(minimum_role):
        raise PermissionDenied(
            "project permission denied",
            details={"project_id": project_id, "required_role": minimum_role, "actual_role": role or "none"},
        )
    return cast(ProjectRole, role)


def can_discover(role: ProjectRole | None) -> bool:
    return _rank(role) >= _ROLE_RANK["discoverer"]


def can_read(role: ProjectRole | None) -> bool:
    return _rank(role) >= _ROLE_RANK["viewer"]


def _grant_matches_actor(ctx: RequestContext, grant: ProjectGrantRow) -> bool:
    if grant["principal_type"] == "user":
        return grant["principal_id"] == ctx.actor_user_id
    if grant["principal_type"] == "role":
        return grant["principal_id"] in ctx.roles
    return False


def _rank(role: ProjectRole | str | None) -> int:
    return _ROLE_RANK.get(str(role), 0)
