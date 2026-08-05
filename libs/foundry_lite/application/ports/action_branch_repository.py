"""Persistence port for branch-isolated Action object overlays."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foundry_lite.application.action_branch_types import (
    ActionBranchEditRecord,
    ActionBranchEditRow,
    ActionBranchLinkRow,
    ActionBranchLinkWrite,
    ActionBranchObjectRow,
    ActionBranchObjectWrite,
)
from foundry_lite.application.ports.object_read_repository import ObjectLinkRow
from foundry_lite.application.ports.transaction_context import TransactionContext


class ActionBranchRepository(Protocol):
    def object_overlay(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        object_type_api_name: str,
        object_id: str,
    ) -> ActionBranchObjectRow | None: ...

    def store_object_overlay(
        self,
        *,
        transaction: TransactionContext,
        record: ActionBranchObjectWrite,
    ) -> ActionBranchObjectRow | None: ...

    def active_base_link(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        link_type_api_name: str,
        from_object_id: str,
        to_object_id: str,
    ) -> ObjectLinkRow | None: ...

    def link_overlay(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        link_type_api_name: str,
        from_object_id: str,
        to_object_id: str,
    ) -> ActionBranchLinkRow | None: ...

    def store_link_overlay(
        self,
        *,
        transaction: TransactionContext,
        record: ActionBranchLinkWrite,
    ) -> ActionBranchLinkRow | None: ...

    def insert_edit(
        self,
        *,
        transaction: TransactionContext,
        record: ActionBranchEditRecord,
    ) -> ActionBranchEditRow | None: ...

    def list_object_overlays(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
    ) -> list[ActionBranchObjectRow]: ...

    def list_link_overlays(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
    ) -> list[ActionBranchLinkRow]: ...

    def link_overlays_from(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
        limit: int,
    ) -> list[ActionBranchLinkRow]: ...

    def link_overlays_to(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        link_type_api_name: str,
        to_api_name: str,
        to_object_id: str,
        limit: int,
    ) -> list[ActionBranchLinkRow]: ...

    def object_overlays_for_ids(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        object_type_api_name: str,
        object_ids: Sequence[str],
    ) -> list[ActionBranchObjectRow]: ...

    def list_edits(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
    ) -> list[ActionBranchEditRow]: ...


class UnavailableActionBranchRepository:
    """Default adapter used by custom runtimes that do not enable Action branches."""

    def __getattr__(self, name: str) -> object:
        del name
        raise RuntimeError("Action branch repository unavailable")
