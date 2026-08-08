"""Security policy helpers for policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied

#: Resolves the active ontology's classified properties for a tenant. The
#: composition root wires this to the ontology repository so the policy never
#: imports a database/vendor SDK. Each row carries ``object_type_api_name``,
#: ``property_api_name``, ``column_name`` and ``classification``; rows may
#: additionally carry ``segment_required_role`` for properties mapped to a
#: multi-datasource segment gated by ``requiredRole`` (admin implicit).
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
        "dataset:read:confidential": {"admin", "data_engineer", "ops_manager", "finance"},
        "dataset:read:restricted": {"admin", "data_engineer", "ops_manager"},
        "dataset:write": {"admin", "data_engineer"},
        "dataset:webhook_ingest": {"admin", "data_engineer", "connector_ingest"},
        "source:read": {"admin", "data_engineer", "ops_manager"},
        "source:write": {"admin", "data_engineer"},
        "connector:read": {"admin", "data_engineer", "ops_manager"},
        "connector:write": {"admin", "data_engineer"},
        "transform:run": {"admin", "data_engineer", "ops_manager"},
        "pipeline:read": {"admin", "data_engineer", "ops_manager", "viewer"},
        "pipeline:write": {"admin", "data_engineer"},
        "pipeline:review": {"admin", "ops_manager"},
        "pipeline:deploy": {"admin", "ops_manager"},
        "pipeline:run": {"admin", "data_engineer", "ops_manager"},
        "ontology:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        "ontology:validate": {"admin", "data_engineer"},
        "ontology:activate": {"admin", "data_engineer"},
        "object:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        "object:edit": {"admin", "data_engineer", "ops_manager"},
        "object:delete": {"admin", "ops_manager"},
        "object:edit:sensitive": {"admin", "ops_manager", "finance"},
        "link:edit": {"admin", "data_engineer", "ops_manager"},
        "function:execute": {"admin", "data_engineer", "ops_manager"},
        "action:effect:execute": {"admin", "ops_manager"},
        "action:effect:read": {"admin", "data_engineer", "ops_manager"},
        "action:effect:manage": {"admin", "ops_manager"},
        "action:notification-policy:read": {"admin", "data_engineer", "ops_manager"},
        "action:notification-policy:manage": {"admin", "data_engineer"},
        # Run observation is deliberately separate from execution. The run
        # ledger can contain operational failure/effect evidence, so viewers do
        # not receive it merely because they may browse the Ontology catalog.
        "action:run:read": {"admin", "data_engineer", "ops_manager"},
        "action:log:read": {"admin", "data_engineer", "ops_manager"},
        "action:revert": {"admin", "ops_manager"},
        # explain exposes base/edit property layers plus operational lineage and
        # source-run metadata, so it is gated above plain read (viewers are excluded).
        "object:explain": {"admin", "data_engineer", "ops_manager", "finance"},
        "object:index": {"admin", "data_engineer", "ops_manager"},
        "object:set:manage": {"admin", "data_engineer", "ops_manager"},
        "insight:read": {"admin", "data_engineer", "ops_manager", "finance"},
        "insight:create": {"admin", "data_engineer"},
        "insight:review": {"admin", "ops_manager"},
        "media:read": {"admin", "data_engineer", "ops_manager", "viewer", "finance"},
        "media:search": {"admin", "data_engineer", "ops_manager"},
        # AIP eval runs record durable release-gate evidence, so they are
        # operator/engineer only (viewers and finance are excluded). Release
        # promotion is a deploy-gate decision, restricted further to admins and
        # ops managers, mirroring action execution and operations retry.
        "aip:evals:run": {"admin", "data_engineer", "ops_manager"},
        "aip:mcp:confirm": {"admin", "data_engineer", "ops_manager"},
        "aip:releases:promote": {"admin", "ops_manager"},
        # Binding a media version onto an object property writes into the object graph, so it is
        # gated above plain reads (viewers are excluded) to stop reference poisoning by callers
        # who may not write the holder.
        "media:reference:bind": {"admin", "data_engineer", "ops_manager"},
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

    def require_dataset_classification(self, ctx: RequestContext, classification: object) -> None:
        """Apply a dataset-level ACL in addition to column masking."""

        self.require(ctx, _dataset_classification_permission(classification))

    def can_read_dataset_classification(self, ctx: RequestContext, classification: object) -> bool:
        """Return whether a dataset may appear in a caller's resource listing."""

        return self.decide(ctx, _dataset_classification_permission(classification)).allowed

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
        # Segment masking nulls the value (Palantir semantics: the segment's
        # values simply do not exist for this caller); classification masking
        # keeps its explicit sentinel and wins when both apply so audit-safe
        # redaction stays observable.
        segment_masked = self.segment_masked_property_names(ctx, object_type)
        classification_masked: set[str] = (
            set() if _can_read_sensitive(ctx) else self.sensitive_property_names(ctx, object_type)
        )
        masked = _mask_null(properties, segment_masked - classification_masked)
        return _mask(masked, classification_masked)

    def mask_sensitive_properties(
        self, ctx: RequestContext, object_type: str, properties: dict[str, object]
    ) -> dict[str, object]:
        """Mask values that should never be copied into durable audit evidence."""
        return _mask(properties, self.sensitive_property_names(ctx, object_type))

    def masked_property_names(self, ctx: RequestContext, object_type: str) -> set[str]:
        """Return properties that must not be displayed or used for inference.

        Segment-masked properties (datasource ``requiredRole`` the caller lacks)
        compose with classification masking: both are excluded from filters,
        ordering, aggregation, and search indexing identically.
        """
        segment_masked = self.segment_masked_property_names(ctx, object_type)
        if _can_read_sensitive(ctx):
            return segment_masked
        return self.sensitive_property_names(ctx, object_type) | segment_masked

    def sensitive_property_names(self, ctx: RequestContext, object_type: str) -> set[str]:
        """Properties classified sensitive in the tenant's active ontology."""
        return self._sensitive_sets(ctx).sensitive_by_type.get(object_type, set())

    def segment_masked_property_names(self, ctx: RequestContext, object_type: str) -> set[str]:
        """Properties whose datasource segment the caller may not view.

        A property mapped to a datasource declaring ``requiredRole`` is visible
        only to callers holding that role (admin implicit); everyone else sees
        its VALUE as null while the property stays in the schema. The same set
        gates action mutations: editing requires viewing the segment.
        """
        if ctx.has_role("admin"):
            return set()
        held = set(ctx.roles)
        roles_by_property = self._sensitive_sets(ctx).segment_roles_by_type.get(object_type, {})
        return {name for name, role in roles_by_property.items() if role not in held}

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
        return self._sensitive_sets(ctx).sensitive_columns

    def _sensitive_sets(self, ctx: RequestContext) -> _PropertyPolicySets:
        """Resolve classification and segment-role property sets from the ontology."""
        if self._classification_provider is None:
            if not self._allow_unwired_classification_provider:
                raise PermissionDenied(
                    "classification provider is not configured",
                    details={"classification_provider": "missing"},
                )
            return _PropertyPolicySets()
        sets = _PropertyPolicySets()
        for row in self._classification_provider(ctx.tenant_id):
            _collect_segment_role(sets, row)
            _collect_classification(sets, row)
        return sets


@dataclass
class _PropertyPolicySets:
    """Property-level policy inputs resolved from the active ontology."""

    sensitive_by_type: dict[str, set[str]] = field(default_factory=lambda: {})
    sensitive_columns: set[str] = field(default_factory=lambda: set())
    #: object type -> property -> datasource segment ``requiredRole``
    segment_roles_by_type: dict[str, dict[str, str]] = field(default_factory=lambda: {})


def _dataset_classification_permission(classification: object) -> str:
    value = str(classification or "public").strip().upper()
    if value in {"UNCLASSIFIED", "PUBLIC", "INTERNAL"}:
        return "dataset:read"
    if value == "CONFIDENTIAL":
        return "dataset:read:confidential"
    return "dataset:read:restricted"


def _collect_classification(sets: _PropertyPolicySets, row: Mapping[str, object]) -> None:
    classification = row.get("classification")
    if classification is None:
        return
    if not _is_known_classification(classification):
        raise PermissionDenied(
            "unsupported property classification",
            details={"classification": classification},
        )
    if not _is_sensitive(classification):
        return
    object_type = row.get("object_type_api_name")
    property_name = row.get("property_api_name")
    if isinstance(object_type, str) and isinstance(property_name, str):
        sets.sensitive_by_type.setdefault(object_type, set()).add(property_name)
    column_name = row.get("column_name")
    if isinstance(column_name, str):
        sets.sensitive_columns.add(column_name)


def _collect_segment_role(sets: _PropertyPolicySets, row: Mapping[str, object]) -> None:
    role = row.get("segment_required_role")
    if role is None:
        return
    if not isinstance(role, str) or not role:
        raise PermissionDenied(
            "unsupported datasource segment role",
            details={"segment_required_role": str(role)},
        )
    object_type = row.get("object_type_api_name")
    property_name = row.get("property_api_name")
    if isinstance(object_type, str) and isinstance(property_name, str):
        sets.segment_roles_by_type.setdefault(object_type, {})[property_name] = role


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


def _mask_null(values: dict[str, object], names: set[str]) -> dict[str, object]:
    """Null out segment-masked values: the caller cannot view that datasource."""
    masked = dict(values)
    for name in names:
        if name in masked:
            masked[name] = None
    return masked
