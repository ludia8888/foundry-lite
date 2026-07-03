"""Security policy helpers for policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied

#: Resolves the active ontology's classified properties for a tenant. The
#: composition root wires this to the ontology repository so the policy never
#: imports a database/vendor SDK. Each row carries ``object_type_api_name``,
#: ``property_api_name``, ``column_name`` and ``classification``.
ClassificationProvider = Callable[[str], Sequence[Mapping[str, object]]]

#: Resolves the ``permissions.allowedRoles`` declared on one action type of the
#: tenant's active ontology, called as ``(tenant_id, action_api_name)``. ``None``
#: means the active ontology declares no roles for that action, so enforcement
#: falls back to the static ``permission_roles`` map. The composition root wires
#: this to the ontology repository (mirroring ``ClassificationProvider``) so a
#: newly declared action in YAML is enforced without any policy code change.
ActionRoleProvider = Callable[[str, str], Sequence[str] | None]

#: Permissions with this prefix gate one action type each; their allowed roles
#: are declared per action in ontology YAML rather than in the static map.
ACTION_EXECUTE_PERMISSION_PREFIX = "action:execute:"

#: Classifications that make a property/column sensitive. This is the only
#: place sensitivity is *defined*; which properties carry it comes from the
#: ontology, so a new finance/PII property in YAML is protected automatically.
SENSITIVE_CLASSIFICATIONS = frozenset({"finance", "pii"})
KNOWN_PROPERTY_CLASSIFICATIONS = SENSITIVE_CLASSIFICATIONS | frozenset({"public"})

_MASKED = "***MASKED***"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class PolicyService:
    """Small v1 policy engine: tenant context, RBAC, and property masking."""

    permission_roles: dict[str, set[str]] = {
        "dataset:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance", "connector_ingest"},
        "dataset:write": {"admin", "data_engineer"},
        "dataset:webhook_ingest": {"admin", "data_engineer", "connector_ingest"},
        "source:read": {"admin", "data_engineer", "ops_manager"},
        "source:write": {"admin", "data_engineer"},
        "connector:read": {"admin", "data_engineer", "ops_manager"},
        "connector:write": {"admin", "data_engineer"},
        "transform:run": {"admin", "data_engineer"},
        "ontology:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        "ontology:validate": {"admin", "data_engineer"},
        "ontology:activate": {"admin", "data_engineer"},
        "object:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        # explain exposes base/edit property layers plus operational lineage and
        # source-run metadata, so it is gated above plain read (viewers are excluded).
        "object:explain": {"admin", "data_engineer", "ops_manager", "finance"},
        "object:index": {"admin", "data_engineer", "ops_manager"},
        "object:set:manage": {"admin", "data_engineer", "ops_manager"},
        "insight:read": {"admin", "data_engineer", "ops_manager", "finance"},
        "insight:create": {"admin", "data_engineer"},
        "insight:review": {"admin", "ops_manager"},
        "media:search": {"admin", "data_engineer", "ops_manager"},
        "action:execute:ApproveOrder": {"admin", "ops_manager"},
        "developer_console:read": {"admin", "data_engineer", "ops_manager"},
        "developer_console:manage": {"admin", "data_engineer"},
        "materialization:run": {"admin", "data_engineer", "ops_manager"},
        # Operations exposes raw run/writeback/outbox/audit rows, so reads are
        # operator-only (viewers and finance are excluded) and split from retry.
        "operations:read:summary": {"admin", "data_engineer", "ops_manager"},
        "operations:read:detail": {"admin", "data_engineer", "ops_manager"},
        "operations:retry": {"admin", "ops_manager"},
    }

    def __init__(
        self,
        classification_provider: ClassificationProvider | None = None,
        action_role_provider: ActionRoleProvider | None = None,
        *,
        allow_unwired_classification_provider: bool = False,
        allow_unwired_action_role_provider: bool = False,
    ) -> None:
        self._classification_provider = classification_provider
        self._action_role_provider = action_role_provider
        self._allow_unwired_classification_provider = allow_unwired_classification_provider
        self._allow_unwired_action_role_provider = allow_unwired_action_role_provider

    def decide(self, ctx: RequestContext, permission: str) -> PolicyDecision:
        """Role check: ontology-declared action roles first, static map otherwise."""
        allowed_roles, source = self._allowed_roles(ctx, permission)
        if any(role in allowed_roles for role in ctx.roles):
            return PolicyDecision(True, f"role matched one of {sorted(allowed_roles)} ({source})")
        return PolicyDecision(False, f"requires one of {sorted(allowed_roles)} ({source})")

    def require(self, ctx: RequestContext, permission: str) -> None:
        """Raise the typed domain error services expect when a role check fails."""
        decision = self.decide(ctx, permission)
        if not decision.allowed:
            raise PermissionDenied(
                f"permission denied for {permission}",
                details={"permission": permission, "reason": decision.reason},
            )

    def _allowed_roles(self, ctx: RequestContext, permission: str) -> tuple[set[str], str]:
        """Resolve (allowed roles, source) so deny reasons explain where roles came from.

        Order matters for fail-closed semantics: an action's YAML-declared
        ``allowedRoles`` win over the static map, and an action neither declared in
        the active ontology nor listed in the static map stays admin-only (the map's
        default for unknown permissions).
        """
        declared = self._ontology_action_roles(ctx, permission)
        if declared is not None:
            return declared, "declared by active ontology action definition"
        if permission in self.permission_roles:
            return self.permission_roles[permission], "static permission map"
        return {"admin"}, "unknown permission fails closed to admin"

    def _ontology_action_roles(self, ctx: RequestContext, permission: str) -> set[str] | None:
        """Roles the active ontology declares for an ``action:execute:*`` permission.

        Admin is added implicitly to match the static map convention (every entry
        grants admin). An unwired provider fails closed instead of silently
        ignoring YAML ``allowedRoles`` — the exact bug this resolution prevents.
        """
        if not permission.startswith(ACTION_EXECUTE_PERMISSION_PREFIX):
            return None
        if self._action_role_provider is None:
            if self._allow_unwired_action_role_provider:
                return None
            raise PermissionDenied(
                "action role provider is not configured",
                details={"permission": permission, "action_role_provider": "missing"},
            )
        action_api_name = permission.removeprefix(ACTION_EXECUTE_PERMISSION_PREFIX)
        declared = self._action_role_provider(ctx.tenant_id, action_api_name)
        if declared is None:
            return None
        return set(declared) | {"admin"}

    def mask_properties(
        self,
        ctx: RequestContext,
        object_type: str,
        properties: dict[str, object],
    ) -> dict[str, object]:
        return _mask(properties, self.masked_property_names(ctx, object_type))

    def mask_sensitive_properties(
        self, ctx: RequestContext, object_type: str, properties: dict[str, object]
    ) -> dict[str, object]:
        """Mask values that should never be copied into durable audit evidence."""
        return _mask(properties, self.sensitive_property_names(ctx, object_type))

    def masked_property_names(self, ctx: RequestContext, object_type: str) -> set[str]:
        """Return properties that must not be displayed or used for inference."""
        if _can_read_sensitive(ctx):
            return set()
        return self.sensitive_property_names(ctx, object_type)

    def sensitive_property_names(self, ctx: RequestContext, object_type: str) -> set[str]:
        """Properties classified sensitive in the tenant's active ontology."""
        return self._sensitive_sets(ctx)[0].get(object_type, set())

    def mask_columns(self, ctx: RequestContext, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        """Apply the same sensitive classification to raw dataset rows (preview)."""
        masked_columns = self.masked_column_names(ctx)
        return [_mask(row, masked_columns) for row in rows]

    def masked_column_names(self, ctx: RequestContext) -> set[str]:
        """Dataset columns to mask for the caller (empty for finance/admin)."""
        if _can_read_sensitive(ctx):
            return set()
        return self.sensitive_column_names(ctx)

    def sensitive_column_names(self, ctx: RequestContext) -> set[str]:
        """Dataset columns backing properties classified sensitive in the active ontology."""
        return self._sensitive_sets(ctx)[1]

    def _sensitive_sets(self, ctx: RequestContext) -> tuple[dict[str, set[str]], set[str]]:
        """Resolve (sensitive properties by object type, sensitive columns) from the ontology."""
        if self._classification_provider is None:
            if not self._allow_unwired_classification_provider:
                raise PermissionDenied(
                    "classification provider is not configured",
                    details={"classification_provider": "missing"},
                )
            return {}, set()
        properties_by_type: dict[str, set[str]] = {}
        columns: set[str] = set()
        for row in self._classification_provider(ctx.tenant_id):
            classification = row.get("classification")
            if classification is None:
                continue
            if not _is_known_classification(classification):
                raise PermissionDenied(
                    "unsupported property classification",
                    details={"classification": classification},
                )
            if not _is_sensitive(classification):
                continue
            object_type = row.get("object_type_api_name")
            property_name = row.get("property_api_name")
            if isinstance(object_type, str) and isinstance(property_name, str):
                properties_by_type.setdefault(object_type, set()).add(property_name)
            column_name = row.get("column_name")
            if isinstance(column_name, str):
                columns.add(column_name)
        return properties_by_type, columns


def _can_read_sensitive(ctx: RequestContext) -> bool:
    return ctx.has_role("finance") or ctx.has_role("admin")


def _is_sensitive(classification: object) -> bool:
    return isinstance(classification, str) and classification in SENSITIVE_CLASSIFICATIONS


def _is_known_classification(classification: object) -> bool:
    return isinstance(classification, str) and classification in KNOWN_PROPERTY_CLASSIFICATIONS


def _mask(values: dict[str, object], names: set[str]) -> dict[str, object]:
    masked = dict(values)
    for name in names:
        if name in masked:
            masked[name] = _MASKED
    return masked
