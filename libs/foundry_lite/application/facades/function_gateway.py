"""Thin facade entrypoints for ontology function execution workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.function_execution_service import (
    FunctionExecutionResult,
    FunctionExecutionService,
)
from foundry_lite.domain.context import RequestContext


class FunctionGateway:
    """Facade for executing registered ontology functions."""

    def __init__(self, function_execution_service: FunctionExecutionService) -> None:
        self._function_execution_service = function_execution_service

    def execute(
        self,
        function_api_name: str,
        *,
        inputs: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> FunctionExecutionResult:
        return self._function_execution_service.execute_function(function_api_name, inputs=inputs, ctx=ctx)
