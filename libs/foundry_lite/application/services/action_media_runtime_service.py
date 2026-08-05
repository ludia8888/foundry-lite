"""Canonical media resolution and atomic binding for Action execution paths."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.action_repository import ActionRepository, ObjectEditRow
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRepository
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.services.action_media_bindings import (
    bind_action_media_references,
    bind_reverted_action_media_references,
)
from foundry_lite.application.services.action_media_parameters import resolve_action_media_parameters
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.context import RequestContext


class ActionMediaRuntimeService(CoreService):
    """Validate immutable references and synchronize holder bindings at commit."""

    required_dependencies = (
        "policy",
        "action_repository",
        "media_repository",
        "media_reference_binding_repository",
    )
    required_collaborators = ("runtime_service",)
    action_repository: ActionRepository
    media_repository: MediaRepository
    media_reference_binding_repository: MediaReferenceBindingRepository
    runtime_service: ActionRuntimeBoundary

    def resolve_parameters(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        return resolve_action_media_parameters(transaction, ctx, self.policy, self.media_repository, contract, values)

    def resolve_command(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        command: ActionApplyCommand,
    ) -> ActionApplyCommand:
        return replace(command, params=self.resolve_parameters(transaction, ctx, contract, command.params))

    def bind_plan_references(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        plan: EditPlan,
    ) -> None:
        bind_action_media_references(
            transaction,
            ctx,
            action_run_id,
            plan,
            self.media_repository,
            self.media_reference_binding_repository,
            self.runtime_service,
        )

    def bind_reverted_references(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        edits: list[ObjectEditRow],
    ) -> None:
        bind_reverted_action_media_references(
            transaction,
            ctx,
            action_run_id,
            edits,
            self.action_repository,
            self.media_repository,
            self.media_reference_binding_repository,
            self.runtime_service,
        )
