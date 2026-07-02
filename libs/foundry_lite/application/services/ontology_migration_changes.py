"""Compatibility exports for ontology migration domain change constructors."""

from __future__ import annotations

from foundry_lite.domain.ontology.migration_changes import (
    blocked_action_removed,
    blocked_action_target_changed,
    blocked_link_backing_changed,
    blocked_link_cardinality_changed,
    blocked_link_endpoint_changed,
    blocked_link_removed,
    blocked_object_removed,
    blocked_parameter_became_required,
    blocked_parameter_removed,
    blocked_parameter_type_changed,
    blocked_primary_key_changed,
    blocked_property_removed,
    blocked_property_rename,
    blocked_property_type_change,
    blocked_required_parameter_added,
    warning_object_reindex,
    warning_optional_parameter_added,
    warning_parameter_became_optional,
    warning_property_deprecated,
    warning_property_reindex,
)

__all__ = [
    "blocked_action_removed",
    "blocked_action_target_changed",
    "blocked_link_backing_changed",
    "blocked_link_cardinality_changed",
    "blocked_link_endpoint_changed",
    "blocked_link_removed",
    "blocked_object_removed",
    "blocked_parameter_became_required",
    "blocked_parameter_removed",
    "blocked_parameter_type_changed",
    "blocked_primary_key_changed",
    "blocked_property_removed",
    "blocked_property_rename",
    "blocked_property_type_change",
    "blocked_required_parameter_added",
    "warning_object_reindex",
    "warning_optional_parameter_added",
    "warning_parameter_became_optional",
    "warning_property_deprecated",
    "warning_property_reindex",
]
