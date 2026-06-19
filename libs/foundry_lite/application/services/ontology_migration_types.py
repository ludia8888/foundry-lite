"""Payload types for ontology migration compatibility planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from foundry_lite.application.primitives import _json_hash
from foundry_lite.domain.errors import ValidationFailed

ActionParameterMap = Mapping[str, object]


@dataclass(frozen=True)
class OntologyMigrationChange:
    """One ontology shape change classified for compatibility evidence."""

    kind: str
    path: str
    status: str
    reason: str
    has_sdk_major_version_requirement: bool = False
    has_object_reindex_requirement: bool = False
    details: Mapping[str, object] = field(default_factory=dict)

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


def _consumer_compatibility(plan: OntologyMigrationPlan) -> str:
    if plan.blocking_changes:
        return "blocked_until_ontology_migration_plan"
    if plan.warning_changes or plan.reindex_operations:
        return "compatible_with_warning"
    return "compatible"


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
