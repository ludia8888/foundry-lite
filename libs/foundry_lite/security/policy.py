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
        "operations:retry": {"admin", "ops_manager"},
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
        for property_name in self.masked_property_names(ctx, object_type):
            if property_name in masked:
                masked[property_name] = "***MASKED***"
        return masked

    def mask_sensitive_properties(self, object_type: str, properties: dict[str, object]) -> dict[str, object]:
        """Mask values that should never be copied into durable audit evidence."""
        masked = dict(properties)
        for property_name in self.sensitive_property_names(object_type):
            if property_name in masked:
                masked[property_name] = "***MASKED***"
        return masked

    def masked_property_names(self, ctx: RequestContext, object_type: str) -> set[str]:
        """Return properties that must not be displayed or used for inference."""

        if not (ctx.has_role("finance") or ctx.has_role("admin")):
            return self.sensitive_property_names(object_type)
        return set()

    def sensitive_property_names(self, object_type: str) -> set[str]:
        """Return properties that remain sensitive even for audit storage."""
        if object_type == "Order":
            return {"margin"}
        return set()
