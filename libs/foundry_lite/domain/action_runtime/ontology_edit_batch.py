"""Typed edit batch returned by a version-pinned Action function.

Function runtimes are compute-only.  They return this value and the Action
runtime translates it into the same immutable ``EditPlan`` used by rule-backed
actions.  Only the Action committer is allowed to write Ontology objects or
links.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.action_runtime.edit_plan import (
    EditPlan,
    LinkCreate,
    LinkDelete,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
    validate_edit_plan,
)
from foundry_lite.domain.errors import ValidationFailed

ONTOLOGY_EDIT_BATCH_EDIT_LIMIT = 10_000
ONTOLOGY_EDIT_BATCH_OBJECT_TYPE_LIMIT = 50


@dataclass(frozen=True, slots=True)
class OntologyEditBatch:
    """Immutable function result plus provenance used for re-validation."""

    edits: tuple[Mapping[str, object], ...]
    read_set_versions: Mapping[str, int]
    provenance: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> OntologyEditBatch:
        edits = tuple(_mapping(item, "edits") for item in _sequence(payload.get("edits"), "edits"))
        if not edits:
            raise ValidationFailed("ontology edit batch must contain at least one edit")
        batch = cls(
            edits=edits,
            read_set_versions=_read_set(payload.get("readSetVersions", {})),
            provenance=_optional_mapping(payload.get("provenance"), "provenance"),
        )
        _require_edit_limits(batch.edits)
        return batch

    @classmethod
    def combine(cls, batches: Sequence[OntologyEditBatch]) -> OntologyEditBatch:
        """Combine compute-only results; the caller still performs one atomic commit."""
        if not batches:
            raise ValidationFailed("at least one ontology edit batch is required")
        edits = tuple(edit for batch in batches for edit in batch.edits)
        read_set: dict[str, int] = {}
        for batch in batches:
            _merge_read_set(read_set, batch.read_set_versions)
        _require_edit_limits(edits)
        return cls(
            edits=edits,
            read_set_versions=read_set,
            provenance={"invocations": [dict(batch.provenance) for batch in batches]},
        )

    def to_edit_plan(self, *, operation_prefix: str) -> EditPlan:
        buckets = _EditBuckets()
        for index, edit in enumerate(self.edits):
            buckets.append(edit, operation_key=f"{operation_prefix}:{index}")
        plan = buckets.plan(self.read_set_versions)
        validate_edit_plan(plan)
        return plan

    def to_payload(self) -> dict[str, object]:
        return {
            "edits": [dict(edit) for edit in self.edits],
            "readSetVersions": dict(self.read_set_versions),
            "provenance": dict(self.provenance),
        }


class _EditBuckets:
    def __init__(self) -> None:
        self.creates: list[ObjectCreate] = []
        self.modifies: list[ObjectModify] = []
        self.deletes: list[ObjectDelete] = []
        self.link_creates: list[LinkCreate] = []
        self.link_deletes: list[LinkDelete] = []

    def append(self, edit: Mapping[str, object], *, operation_key: str) -> None:
        kind = _required_text(edit, "kind")
        builders = {
            "createObject": self._create,
            "modifyObject": self._modify,
            "deleteObject": self._delete,
            "createLink": self._create_link,
            "deleteLink": self._delete_link,
        }
        builder = builders.get(kind)
        if builder is None:
            raise ValidationFailed("unsupported ontology edit batch operation", details={"kind": kind})
        builder(edit, operation_key)

    def plan(self, read_set_versions: Mapping[str, int]) -> EditPlan:
        return EditPlan(
            objects_to_create=tuple(self.creates),
            objects_to_modify=tuple(self.modifies),
            objects_to_delete=tuple(self.deletes),
            links_to_create=tuple(self.link_creates),
            links_to_delete=tuple(self.link_deletes),
            read_set_versions=read_set_versions,
        )

    def _create(self, edit: Mapping[str, object], operation_key: str) -> None:
        self.creates.append(
            ObjectCreate(
                operation_key,
                _rule_id(edit),
                _required_text(edit, "objectType"),
                edit.get("primaryKey"),
                _optional_mapping(edit.get("properties"), "properties"),
            )
        )

    def _modify(self, edit: Mapping[str, object], operation_key: str) -> None:
        self.modifies.append(
            ObjectModify(
                operation_key,
                _rule_id(edit),
                _required_text(edit, "objectType"),
                _required_text(edit, "objectId"),
                _required_version(edit),
                _optional_mapping(edit.get("patch"), "patch"),
                _optional_bool(edit.get("createIfAbsent"), "createIfAbsent"),
            )
        )

    def _delete(self, edit: Mapping[str, object], operation_key: str) -> None:
        self.deletes.append(
            ObjectDelete(
                operation_key,
                _rule_id(edit),
                _required_text(edit, "objectType"),
                _required_text(edit, "objectId"),
                _required_version(edit),
            )
        )

    def _create_link(self, edit: Mapping[str, object], operation_key: str) -> None:
        self.link_creates.append(_link_create(edit, operation_key))

    def _delete_link(self, edit: Mapping[str, object], operation_key: str) -> None:
        self.link_deletes.append(_link_delete(edit, operation_key))


def _link_create(edit: Mapping[str, object], operation_key: str) -> LinkCreate:
    return LinkCreate(
        operation_key,
        _rule_id(edit),
        _required_text(edit, "linkType"),
        _required_text(edit, "sourceObjectId"),
        _required_text(edit, "targetObjectId"),
    )


def _link_delete(edit: Mapping[str, object], operation_key: str) -> LinkDelete:
    return LinkDelete(
        operation_key,
        _rule_id(edit),
        _required_text(edit, "linkType"),
        _required_text(edit, "sourceObjectId"),
        _required_text(edit, "targetObjectId"),
    )


def _read_set(raw: object) -> dict[str, int]:
    payload = _optional_mapping(raw, "readSetVersions")
    result: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValidationFailed("read set versions must be non-negative integers", details={"key": key})
        result[str(key)] = value
    return result


def _merge_read_set(target: dict[str, int], source: Mapping[str, int]) -> None:
    for key, version in source.items():
        existing = target.get(key)
        if existing is not None and existing != version:
            raise ValidationFailed(
                "ontology edit batches disagree on a read-set version",
                details={"object": key, "firstVersion": existing, "nextVersion": version},
            )
        target[key] = version


def _require_edit_limits(edits: Sequence[Mapping[str, object]]) -> None:
    if len(edits) > ONTOLOGY_EDIT_BATCH_EDIT_LIMIT:
        raise ValidationFailed(
            "ontology edit batch edit limit exceeded",
            details={"editCount": len(edits), "editLimit": ONTOLOGY_EDIT_BATCH_EDIT_LIMIT},
        )
    object_types = {str(edit["objectType"]) for edit in edits if isinstance(edit.get("objectType"), str)}
    if len(object_types) > ONTOLOGY_EDIT_BATCH_OBJECT_TYPE_LIMIT:
        raise ValidationFailed(
            "ontology edit batch object type limit exceeded",
            details={
                "objectTypeCount": len(object_types),
                "objectTypeLimit": ONTOLOGY_EDIT_BATCH_OBJECT_TYPE_LIMIT,
            },
        )


def _required_version(edit: Mapping[str, object]) -> int:
    value = edit.get("expectedVersion")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationFailed("ontology edit expectedVersion must be a non-negative integer")
    return value


def _rule_id(edit: Mapping[str, object]) -> str:
    value = edit.get("ruleId", "function:edit")
    if not isinstance(value, str) or not value:
        raise ValidationFailed("ontology edit ruleId must be a non-empty string")
    return value


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed(f"ontology edit {key} must be a non-empty string")
    return value


def _optional_bool(raw: object, field: str) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ValidationFailed(f"ontology edit {field} must be a boolean")
    return raw


def _optional_mapping(raw: object, field: str) -> Mapping[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"ontology edit batch {field} must be an object")
    return cast(Mapping[str, object], raw)


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"ontology edit batch {field} entries must be objects")
    return cast(Mapping[str, object], raw)


def _sequence(raw: object, field: str) -> Sequence[object]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed(f"ontology edit batch {field} must be a list")
    return cast(Sequence[object], raw)
