"""Pure ontology migration compatibility payloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from foundry_lite.domain.errors import ValidationFailed

ActionParameterMap = Mapping[str, object]
OBJECT_REINDEX_COMPLETE = "object_reindex_complete"
OBJECT_REINDEX_REQUIRED = "object_reindex_required"


@dataclass(frozen=True)
class OntologyMigrationChange:
    """One ontology shape change classified for compatibility evidence."""

    kind: str
    path: str
    status: str
    reason: str
    has_sdk_major_version_requirement: bool = False
    has_object_reindex_requirement: bool = False
    details: Mapping[str, object] = field(default_factory=lambda: {})

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "path": self.path,
            "status": self.status,
            "reason": self.reason,
        }
        if self.has_sdk_major_version_requirement:
            payload["requiresSdkMajorVersion"] = True
        if self.has_object_reindex_requirement:
            payload["requiresObjectReindex"] = True
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(frozen=True)
class OntologyReindexOperation:
    """Deterministic object reindex operation derived from mapping changes."""

    object_type_api_name: str
    reason: str
    changed_fields: tuple[str, ...]
    reindex_key: str

    def to_payload(self) -> dict[str, object]:
        return {
            "objectTypeApiName": self.object_type_api_name,
            "reason": self.reason,
            "changedFields": list(self.changed_fields),
            "reindexKey": self.reindex_key,
        }


@dataclass(frozen=True)
class OntologyMigrationPlan:
    """Compatibility plan for a candidate ontology version."""

    source_ontology_version_id: str | None
    changes: tuple[OntologyMigrationChange, ...]
    reindex_operations: tuple[OntologyReindexOperation, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes or self.reindex_operations)

    @property
    def status(self) -> str:
        if self.blocking_changes:
            return "blocked"
        if self.warning_changes or self.reindex_operations:
            return "compatible_with_warning"
        return "compatible"

    @property
    def blocking_changes(self) -> tuple[OntologyMigrationChange, ...]:
        return tuple(change for change in self.changes if change.status == "blocked")

    @property
    def warning_changes(self) -> tuple[OntologyMigrationChange, ...]:
        return tuple(change for change in self.changes if change.status == "warning")

    @property
    def sdk_compatibility(self) -> str:
        if any(change.has_sdk_major_version_requirement for change in self.blocking_changes):
            return "major_version_required"
        if self.warning_changes or self.reindex_operations:
            return "compatible_with_warning"
        return "compatible"

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "consumerCompatibility": _consumer_compatibility(self),
            "sdkCompatibility": self.sdk_compatibility,
            "changes": [change.to_payload() for change in self.changes],
            "objectReindexPlan": [operation.to_payload() for operation in self.reindex_operations],
        }
        if self.source_ontology_version_id is not None:
            payload["sourceOntologyVersionId"] = self.source_ontology_version_id
        if self.blocking_changes:
            payload["blockedChanges"] = [change.to_payload() for change in self.blocking_changes]
        if self.warning_changes:
            payload["warnings"] = [change.to_payload() for change in self.warning_changes]
        return payload

    def raise_if_blocked(self) -> None:
        if self.blocking_changes:
            raise ValidationFailed(
                "ontology migration requires explicit migration plan",
                details={"ontologyMigration": self.to_payload()},
            )


def object_type_serving_config(plan: OntologyMigrationPlan, api_name: str) -> dict[str, object]:
    """Return object-type config that binds serving reads to a required reindex contract."""
    operations = [operation for operation in plan.reindex_operations if operation.object_type_api_name == api_name]
    if not operations:
        return {}
    payload: dict[str, object] = {
        "servingRecordScope": "object_type_id",
        "servingContractStatus": OBJECT_REINDEX_REQUIRED,
        "objectReindexPlan": [operation.to_payload() for operation in operations],
    }
    if plan.source_ontology_version_id is not None:
        payload["sourceOntologyVersionId"] = plan.source_ontology_version_id
    return payload


def pending_object_reindex_operation(config: Mapping[str, object], reindex_key: str) -> Mapping[str, object] | None:
    """Return the pending operation for a key if the object type still requires it."""
    if config.get("servingContractStatus") != OBJECT_REINDEX_REQUIRED:
        return None
    for operation in _object_reindex_plan(config):
        if operation.get("reindexKey") == reindex_key:
            return operation
    return None


def completed_object_reindex(config: Mapping[str, object], reindex_key: str) -> Mapping[str, object] | None:
    """Return stored completion evidence for a replayed ontology reindex key."""
    if config.get("servingContractStatus") != OBJECT_REINDEX_COMPLETE:
        return None
    completed = config.get("objectReindexCompleted")
    if not isinstance(completed, Mapping):
        return None
    completed_map = cast(Mapping[str, object], completed)
    if completed_map.get("reindexKey") != reindex_key:
        return None
    return completed_map


def complete_object_reindex_config(
    config: Mapping[str, object],
    operation: Mapping[str, object],
    *,
    index_run_id: str,
    dataset_version_id: str,
    completed_at: str,
) -> dict[str, object]:
    """Return object-type config after a required ontology reindex succeeds."""
    next_config = dict(config)
    next_config["servingContractStatus"] = OBJECT_REINDEX_COMPLETE
    next_config["objectReindexCompleted"] = {
        "reindexKey": str(operation["reindexKey"]),
        "indexRunId": index_run_id,
        "datasetVersionId": dataset_version_id,
        "completedAt": completed_at,
        "changedFields": list(_changed_fields(operation)),
    }
    return next_config


def reindex_operation(api_name: str, reindex_fields: Sequence[str]) -> OntologyReindexOperation | None:
    """Return a deterministic reindex operation for changed object mapping fields."""
    fields = tuple(sorted(set(reindex_fields)))
    if not fields:
        return None
    payload = {"objectTypeApiName": api_name, "changedFields": list(fields)}
    return OntologyReindexOperation(
        object_type_api_name=api_name,
        reason="ontology object mapping changed",
        changed_fields=fields,
        reindex_key=f"object_reindex:{api_name}:{_json_hash(payload)[:12]}",
    )


def _consumer_compatibility(plan: OntologyMigrationPlan) -> str:
    if plan.blocking_changes:
        return "blocked_until_ontology_migration_plan"
    if plan.warning_changes or plan.reindex_operations:
        return "compatible_with_warning"
    return "compatible"


def _object_reindex_plan(config: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = config.get("objectReindexPlan")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    raw_items = cast(Sequence[object], value)
    operations: list[Mapping[str, object]] = []
    for item in raw_items:
        if isinstance(item, Mapping):
            operations.append(cast(Mapping[str, object], item))
    return tuple(operations)


def _changed_fields(operation: Mapping[str, object]) -> tuple[str, ...]:
    value = operation.get("changedFields")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    raw_items = cast(Sequence[object], value)
    fields: list[str] = []
    for item in raw_items:
        fields.append(str(item))
    return tuple(fields)


def _json_hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
