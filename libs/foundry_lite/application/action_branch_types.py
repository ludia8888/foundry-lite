"""Typed persistence records for branch-isolated Action object overlays."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict


class ActionBranchObjectRow(TypedDict):
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


@dataclass(frozen=True, slots=True)
class ActionBranchObjectWrite:
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
