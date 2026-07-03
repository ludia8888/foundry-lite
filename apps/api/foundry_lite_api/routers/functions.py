"""Ontology function execution routes (Functions on Objects)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from foundry_lite.application.services.function_execution_service import FunctionExecutionResult
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import FunctionExecuteRequest

router = APIRouter()


@router.post("/api/functions/{function_type}/execute")
def execute_function(
    request: Request,
    function_type: str,
    payload: FunctionExecuteRequest,
) -> FunctionExecutionResult:
    try:
        return runtime.foundry.functions.execute(
            function_type,
            inputs=payload.inputs,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
