"""Persistence port for branch-isolated Action object overlays."""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.action_branch_types import (
    ActionBranchEditRecord,
    ActionBranchEditRow,
    ActionBranchObjectRow,
    ActionBranchObjectWrite,
)
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
