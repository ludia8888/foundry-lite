from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class PolicyService:
    """Small v1 policy engine: tenant context, RBAC, and property masking."""

    permission_roles: dict[str, set[str]] = {
        "dataset:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        "dataset:write": {"admin", "data_engineer"},
        "transform:run": {"admin", "data_engineer"},
        "ontology:activate": {"admin", "data_engineer"},
        "object:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        "action:execute:ApproveOrder": {"admin", "ops_manager"},
        "materialization:run": {"admin", "data_engineer", "ops_manager"},
    }

    def decide(self, ctx: RequestContext, permission: str) -> PolicyDecision:
        allowed_roles = self.permission_roles.get(permission, {"admin"})
        if any(role in allowed_roles for role in ctx.roles):
            return PolicyDecision(True, f"role matched one of {sorted(allowed_roles)}")
        return PolicyDecision(False, f"requires one of {sorted(allowed_roles)}")

    def require(self, ctx: RequestContext, permission: str) -> None:
        decision = self.decide(ctx, permission)
        if not decision.allowed:
            raise PermissionDenied(
                f"permission denied for {permission}",
                details={"permission": permission, "reason": decision.reason},
            )

    def mask_properties(
        self,
        ctx: RequestContext,
        object_type: str,
        properties: dict[str, object],
    ) -> dict[str, object]:
        masked = dict(properties)
        if object_type == "Order" and not (ctx.has_role("finance") or ctx.has_role("admin")):
            if "margin" in masked:
                masked["margin"] = "***MASKED***"
        return masked
