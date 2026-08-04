"""Action bounded-context dependency bundle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from foundry_lite.application.dependency_compat import required_dependency
from foundry_lite.application.ports.action_branch_repository import (
    ActionBranchRepository,
    UnavailableActionBranchRepository,
)
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutor,
    UnavailableActionEffectExecutor,
)
from foundry_lite.application.ports.action_execution_repository import ActionExecutionRepository
from foundry_lite.application.ports.action_function_executor import (
    ActionFunctionExecutor,
    UnavailableActionFunctionExecutor,
)
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.action_run_orchestrator import (
    ActionRunOrchestrator,
    UnavailableActionRunOrchestrator,
)


@dataclass(frozen=True)
class ActionDependencies:
    action_repository: ActionRepository
    action_branch_repository: ActionBranchRepository = field(
        default_factory=lambda: cast(ActionBranchRepository, UnavailableActionBranchRepository())
    )
    action_execution_repository: ActionExecutionRepository | None = None
    action_effect_executor: ActionEffectExecutor = field(default_factory=UnavailableActionEffectExecutor)
    action_function_executor: ActionFunctionExecutor = field(default_factory=UnavailableActionFunctionExecutor)
    action_run_orchestrator: ActionRunOrchestrator = field(default_factory=UnavailableActionRunOrchestrator)


class ActionDependencyAccessors:
    """Typed CoreDependencies accessors kept outside the size-limited root module."""

    action: ActionDependencies

    @property
    def action_repository(self) -> ActionRepository:
        return self.action.action_repository

    @property
    def action_branch_repository(self) -> ActionBranchRepository:
        return self.action.action_branch_repository

    @property
    def action_execution_repository(self) -> ActionExecutionRepository:
        return required_dependency(self.action.action_execution_repository, "Action execution repository unavailable")

    @property
    def action_effect_executor(self) -> ActionEffectExecutor:
        return self.action.action_effect_executor

    @property
    def action_function_executor(self) -> ActionFunctionExecutor:
        return self.action.action_function_executor

    @property
    def action_run_orchestrator(self) -> ActionRunOrchestrator:
        return self.action.action_run_orchestrator
