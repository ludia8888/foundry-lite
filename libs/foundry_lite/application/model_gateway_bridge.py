"""Composition-owned bridge from Pipeline semantics to the governed AIP model boundary."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.language_model import (
    GovernedSemanticModelPort,
    ModelRequest,
    ModelResolution,
    ModelResponse,
)
from foundry_lite.domain.context import RequestContext


@dataclass(frozen=True)
class GovernedSemanticModelBridge:
    """Expose governed resolution and invocation; raw provider adapters stay behind AIP."""

    target: GovernedSemanticModelPort

    def resolve_model(
        self,
        ctx: RequestContext,
        model_alias: str,
        environment: str = "prod",
    ) -> ModelResolution:
        return self.target.resolve_model(ctx, model_alias, environment)

    def invoke(self, ctx: RequestContext, request: ModelRequest) -> ModelResponse:
        return self.target.invoke(ctx, request)
