"""Immutable Ontology edit plan produced from Action IR v2.

An action never writes to a repository directly. Its rules are first resolved
into an immutable ``EditPlan`` — the complete set of object creates/modifies/
deletes and many-to-many link creates/deletes, each carrying a stable operation
key (for idempotent replay), the originating rule id, and the object versions
that were read. The plan is validated as a whole, then a single transactional
committer applies it all-or-nothing. Separating plan from commit lets the whole
edit set be permission-checked, previewed (dry-run), and re-validated against
current data after any function/external call, before anything is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True)
class ObjectRef:
    """A concrete existing object resolved during planning, with its read version."""

    object_type: str
    object_id: str
    version: int


@dataclass(frozen=True)
class ObjectCreate:
    """Create one object; primary_key None means the store generates it."""

    operation_key: str
    rule_id: str
    object_type: str
    primary_key: object | None
    properties: Mapping[str, object]


@dataclass(frozen=True)
class ObjectModify:
    """Modify one existing object under optimistic concurrency."""

    operation_key: str
    rule_id: str
    object_type: str
    object_id: str
    expected_version: int
    patch: Mapping[str, object]
    should_create_if_absent: bool = False


@dataclass(frozen=True)
class ObjectDelete:
    """Delete one existing object under optimistic concurrency."""

    operation_key: str
    rule_id: str
    object_type: str
    object_id: str
    expected_version: int


@dataclass(frozen=True)
class LinkCreate:
    """Create one many-to-many link."""

    operation_key: str
    rule_id: str
    link_type: str
    source_object_id: str
    target_object_id: str


@dataclass(frozen=True)
class LinkDelete:
    """Delete one many-to-many link."""

    operation_key: str
    rule_id: str
    link_type: str
    source_object_id: str
    target_object_id: str


@dataclass(frozen=True)
class CriteriaReadExpectation:
    """Opaque OCC evidence for one linked-object submission-criteria read."""

    reference_key: str
    link_type: str
    direction: str
    anchor_object_type: str
    anchor_object_id: str
    linked_object_type: str
    property_name: str
    aggregation: str
    snapshot_fingerprint: str


@dataclass(frozen=True)
class EditPlan:
    """The complete, immutable set of edits one action submission will commit."""

    objects_to_create: tuple[ObjectCreate, ...] = ()
    objects_to_modify: tuple[ObjectModify, ...] = ()
    objects_to_delete: tuple[ObjectDelete, ...] = ()
    links_to_create: tuple[LinkCreate, ...] = ()
    links_to_delete: tuple[LinkDelete, ...] = ()
    read_set_versions: Mapping[str, int] = field(default_factory=lambda: {})
    criteria_read_expectations: tuple[CriteriaReadExpectation, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.objects_to_create
            or self.objects_to_modify
            or self.objects_to_delete
            or self.links_to_create
            or self.links_to_delete
        )


def _all_operation_keys(plan: EditPlan) -> list[str]:
    keys: list[str] = []
    keys.extend(edit.operation_key for edit in plan.objects_to_create)
    keys.extend(edit.operation_key for edit in plan.objects_to_modify)
    keys.extend(edit.operation_key for edit in plan.objects_to_delete)
    keys.extend(edit.operation_key for edit in plan.links_to_create)
    keys.extend(edit.operation_key for edit in plan.links_to_delete)
    return keys


def _validate_unique_operation_keys(plan: EditPlan) -> None:
    keys = _all_operation_keys(plan)
    if len(keys) != len(set(keys)):
        raise ValidationFailed("edit plan has duplicate operation keys", details={"count": len(keys)})


def _validate_object_ids_touched_once(plan: EditPlan) -> None:
    # Object ids are unique only per type, so key on (object_type, object_id) — matching
    # action_ir._validate_double_create — else a cross-type id coincidence looks like a conflict.
    modified = {(edit.object_type, edit.object_id) for edit in plan.objects_to_modify}
    deleted = {(edit.object_type, edit.object_id) for edit in plan.objects_to_delete}
    conflict = modified & deleted
    if conflict:
        raise ValidationFailed("edit plan modifies and deletes the same object", details={"objects": sorted(conflict)})
    modify_keys = [(edit.object_type, edit.object_id) for edit in plan.objects_to_modify]
    if len(modify_keys) != len(set(modify_keys)):
        raise ValidationFailed("edit plan modifies the same object twice", details={"objects": sorted(modified)})
    delete_keys = [(edit.object_type, edit.object_id) for edit in plan.objects_to_delete]
    if len(delete_keys) != len(set(delete_keys)):
        raise ValidationFailed("edit plan deletes the same object twice", details={"objects": sorted(deleted)})


def _validate_links_reference_live_objects(plan: EditPlan) -> None:
    deleted = {edit.object_id for edit in plan.objects_to_delete}
    for link in plan.links_to_create:
        endpoints = {link.source_object_id, link.target_object_id}
        dangling = endpoints & deleted
        if dangling:
            raise ValidationFailed(
                "edit plan creates a link to an object it deletes",
                details={"linkType": link.link_type, "objectIds": sorted(dangling)},
            )


def validate_edit_plan(plan: EditPlan) -> None:
    """Structural checks on a fully-built plan, independent of the store."""
    if plan.is_empty():
        raise ValidationFailed("edit plan is empty", details={})
    _validate_unique_operation_keys(plan)
    _validate_object_ids_touched_once(plan)
    _validate_links_reference_live_objects(plan)
