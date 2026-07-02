"""Compatibility exports for ontology migration domain types."""

from __future__ import annotations

from foundry_lite.domain.ontology.migration_types import (
    OBJECT_REINDEX_COMPLETE,
    OBJECT_REINDEX_REQUIRED,
    ActionParameterMap,
    OntologyMigrationChange,
    OntologyMigrationPlan,
    OntologyReindexOperation,
    complete_object_reindex_config,
    completed_object_reindex,
    object_type_serving_config,
    pending_object_reindex_operation,
    reindex_operation,
)

__all__ = [
    "OBJECT_REINDEX_COMPLETE",
    "OBJECT_REINDEX_REQUIRED",
    "ActionParameterMap",
    "OntologyMigrationChange",
    "OntologyMigrationPlan",
    "OntologyReindexOperation",
    "complete_object_reindex_config",
    "completed_object_reindex",
    "object_type_serving_config",
    "pending_object_reindex_operation",
    "reindex_operation",
]
