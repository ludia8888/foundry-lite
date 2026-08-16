"""Row-level (instance) security for ontology object types.

Object types may declare ``rowPolicies`` in ontology YAML; each policy grants
one role visibility over the rows matching a filter expressed in the same
filter AST the object query path already understands. Declarations ride in the
object type's config JSON (like ``titleProperty``/``materialization``) so no
schema migration is needed and snapshot/rollback replay preserves them.

Semantics are fail-closed: once a type declares ANY row policy, a non-admin
caller only sees the UNION (OR) of the filters whose role they hold, and a
caller holding none of the declared roles sees zero rows. Types without
declarations behave exactly as before, and admin bypasses row policies the way
it bypasses the rest of the permission map.

Post-load checks reuse ``matches_filter`` — the same predicate the in-memory
filter evaluation uses — so SQL-injected and Python-evaluated policy filters
can never diverge on operator semantics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports import ObjectRecordRow, ObjectTypeRow, PropertyTypeRow
from foundry_lite.application.query_filters import matches_filter, validate_filter_ast
from foundry_lite.domain.errors import ValidationFailed

#: Config JSON key holding the persisted ``rowPolicies`` declarations.
ROW_POLICIES_CONFIG_KEY = "rowPolicies"

#: Admin bypasses row policies, mirroring the static permission map where every
#: entry grants admin.
ROW_POLICY_ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class RowPolicyScope:
    """Resolved row visibility for one caller on one object type.

    ``is_unrestricted`` short-circuits enforcement for types without declarations
    (and for admin); otherwise ``filter_ast`` is the union filter of the
    caller's matching policies, and ``None`` means zero rows are visible.
    """

    is_unrestricted: bool
    filter_ast: Mapping[str, object] | None
    property_data_types: Mapping[str, str] | None = None

    @property
    def hides_all_rows(self) -> bool:
        return not self.is_unrestricted and self.filter_ast is None


UNRESTRICTED_ROWS = RowPolicyScope(is_unrestricted=True, filter_ast=None)
ZERO_ROWS = RowPolicyScope(is_unrestricted=False, filter_ast=None)


def row_policy_scope(
    object_type: ObjectTypeRow,
    roles: Sequence[str],
    property_types: Sequence[PropertyTypeRow] | None = None,
) -> RowPolicyScope:
    """Resolve the caller's row visibility from the persisted declarations."""
    policies = persisted_row_policies(object_type["config"])
    if not policies:
        return UNRESTRICTED_ROWS
    property_data_types = _property_data_types(property_types)
    for _role, policy_filter in policies:
        validate_filter_ast(policy_filter, property_data_types=property_data_types)
    if ROW_POLICY_ADMIN_ROLE in roles:
        return UNRESTRICTED_ROWS
    filters = [policy_filter for role, policy_filter in policies if role in roles]
    if not filters:
        return ZERO_ROWS
    if len(filters) == 1:
        return RowPolicyScope(False, filters[0], property_data_types)
    return RowPolicyScope(False, {"or": filters}, property_data_types)


def _property_data_types(property_types: Sequence[PropertyTypeRow] | None) -> dict[str, str]:
    if property_types is None:
        raise ValidationFailed("row policy property metadata is unavailable")
    return {row["api_name"]: row["data_type"] for row in property_types}


def persisted_row_policies(config: Mapping[str, object]) -> list[tuple[str, Mapping[str, object]]]:
    """Return the ``(role, filter)`` pairs persisted in an object type config.

    A malformed persisted value raises instead of being skipped: silently
    ignoring a broken declaration would fail OPEN (every row visible), the
    exact bug row policies exist to prevent.
    """
    declared = config.get(ROW_POLICIES_CONFIG_KEY)
    if declared is None:
        return []
    if not isinstance(declared, Sequence) or isinstance(declared, str | bytes):
        raise ValidationFailed("object type row policies are malformed")
    return [_persisted_row_policy(policy) for policy in declared]


def _persisted_row_policy(policy: object) -> tuple[str, Mapping[str, object]]:
    if not isinstance(policy, Mapping):
        raise ValidationFailed("object type row policy must be a mapping")
    role = policy.get("role")
    policy_filter = policy.get("filter")
    if not isinstance(role, str) or not role or not isinstance(policy_filter, Mapping):
        raise ValidationFailed("object type row policy requires a role and a filter")
    validate_filter_ast(policy_filter)
    return role, policy_filter


def scoped_filter_ast(
    scope: RowPolicyScope,
    filter_ast: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    """AND the caller's policy filter into an optional user filter.

    Callers must handle ``hides_all_rows`` before calling this; the zero case
    cannot be expressed in the filter AST without inventing a false operator.
    """
    if scope.filter_ast is None:
        return filter_ast
    if filter_ast is None:
        return scope.filter_ast
    return {"and": [scope.filter_ast, filter_ast]}


def row_visible(scope: RowPolicyScope, properties: Mapping[str, object]) -> bool:
    """Post-load check for paths that fetch a row before authorization applies."""
    if scope.is_unrestricted:
        return True
    if scope.filter_ast is None:
        return False
    if scope.property_data_types is None:
        raise ValidationFailed("row policy property metadata is unavailable")
    return matches_filter(properties, scope.filter_ast, property_data_types=scope.property_data_types)


def visible_record(
    record: ObjectRecordRow | None,
    object_type: ObjectTypeRow,
    roles: Sequence[str],
    property_types: Sequence[PropertyTypeRow] | None = None,
) -> ObjectRecordRow | None:
    """Drop a row the caller must not see, indistinguishably from a missing row.

    Action apply/validate and other single-record loaders funnel through this
    so a hidden target surfaces as NotFound rather than leaking existence.
    """
    if record is None:
        return None
    return record if row_visible(row_policy_scope(object_type, roles, property_types), record["properties"]) else None
