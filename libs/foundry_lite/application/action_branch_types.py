"""Typed persistence records for branch-isolated Action object overlays."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict


class ActionBranchObjectRow(TypedDict):
    """Persisted object overlay isolated from the main Ontology branch."""

    id: str
    tenant_id: str
    branch_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    base_object_version: int | None
    overlay_version: int
    properties: dict[str, object]
    deleted: bool
    last_action_run_id: str
    created_at: str
    updated_at: str


class ActionBranchEditRow(TypedDict):
    """Append-only evidence for one branch Action edit."""

    id: str
    tenant_id: str
    branch_id: str
    action_run_id: str
    operation_key: str
    ordinal: int
    edit_kind: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    before: dict[str, object]
    after: dict[str, object]
    created_at: str


class ActionBranchLinkRow(TypedDict):
    """Persisted link overlay isolated from the main Ontology branch."""

    id: str
    tenant_id: str
    branch_id: str
    link_type_id: str
    link_type_api_name: str
    from_object_type_id: str
    from_api_name: str
    from_object_id: str
    to_object_type_id: str
    to_api_name: str
    to_object_id: str
    base_link_version: int | None
    overlay_version: int
    deleted: bool
    last_action_run_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ActionBranchObjectWrite:
    """CAS-protected write request for a branch object overlay."""

    overlay_id: str
    tenant_id: str
    branch_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    base_object_version: int | None
    expected_overlay_version: int | None
    overlay_version: int
    properties: Mapping[str, object]
    is_deleted: bool
    action_run_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ActionBranchEditRecord:
    """Immutable write request for branch edit evidence."""

    edit_id: str
    tenant_id: str
    branch_id: str
    action_run_id: str
    operation_key: str
    ordinal: int
    edit_kind: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    before: Mapping[str, object]
    after: Mapping[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionBranchLinkWrite:
    """CAS-protected write request for a branch link overlay."""

    overlay_id: str
    tenant_id: str
    branch_id: str
    link_type_id: str
    link_type_api_name: str
    from_object_type_id: str
    from_api_name: str
    from_object_id: str
    to_object_type_id: str
    to_api_name: str
    to_object_id: str
    base_link_version: int | None
    expected_overlay_version: int | None
    overlay_version: int
    is_deleted: bool
    action_run_id: str
    created_at: str
    updated_at: str
