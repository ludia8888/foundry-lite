"""AIP builder, agent, eval, and release routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Request
from foundry_lite.application.services.aip.eval_types import AiEvalError, EvalCaseInput
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    AipAgentRunRequest,
    AipBuilderRunRequest,
    AipBuilderValidateRequest,
    AipEvalCaseRequest,
    AipEvalRunRequest,
    AipReleasePromotionRequest,
    JsonObject,
)

router = APIRouter()


@router.post("/api/aip/builder/validate")
def validate_aip_builder(request: Request, payload: AipBuilderValidateRequest) -> JsonObject:
    result = runtime.foundry.aip.validate_builder_payload(
        payload=payload.model_dump(by_alias=True),
        ctx=_ctx(request),
    )
    return result.to_payload()


@router.post("/api/aip/builder/run")
def run_aip_builder(request: Request, payload: AipBuilderRunRequest) -> JsonObject:
    result = runtime.foundry.aip.run_builder_payload(
        payload=payload.model_dump(by_alias=True),
        ctx=_ctx(request),
    )
    return result.to_payload()


@router.post("/api/aip/agent/run")
def run_aip_agent(request: Request, payload: AipAgentRunRequest) -> JsonObject:
    result = runtime.foundry.aip.run_agent_payload(
        payload=payload.model_dump(by_alias=True),
        ctx=_ctx(request),
    )
    return result.to_payload()


@router.post("/api/aip/evals/run")
def run_aip_eval(request: Request, payload: AipEvalRunRequest) -> JsonObject:
    try:
        result = runtime.foundry.aip.run_eval(
            eval_run_id=payload.eval_run_id,
            suite_api_name=payload.suite_api_name,
            suite_version=payload.suite_version,
            suite_description=payload.suite_description,
            agent_version_id=payload.agent_version_id,
            candidate_release_channel=payload.candidate_release_channel,
            cases=tuple(_eval_case(case) for case in payload.cases),
            min_score=payload.min_score,
            required_axes=tuple(payload.required_axes),
            ctx=_ctx(request),
        )
        return cast(JsonObject, asdict(result))
    except AiEvalError as exc:
        raise _handle_error(_aip_eval_validation_error(exc), request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/releases/promote")
def promote_aip_release(request: Request, payload: AipReleasePromotionRequest) -> JsonObject:
    try:
        result = runtime.foundry.aip.promote_agent_release(
            agent_version_id=payload.agent_version_id,
            target_release_channel=payload.target_release_channel,
            eval_run_id=payload.eval_run_id,
            policy_version=payload.policy_version,
            ctx=_ctx(request),
        )
        return cast(JsonObject, asdict(result))
    except AiEvalError as exc:
        raise _handle_error(_aip_eval_validation_error(exc), request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _eval_case(payload: AipEvalCaseRequest) -> EvalCaseInput:
    return EvalCaseInput(
        case_api_name=payload.case_api_name,
        axis=payload.axis,
        input_json=payload.input_json,
        expected_json=payload.expected_json,
        actual_json=payload.actual_json,
        rubric_json=payload.rubric_json,
        tags=tuple(payload.tags),
        sample_index=payload.sample_index,
        evaluator=payload.evaluator,
        weight=payload.weight,
    )


def _aip_eval_validation_error(exc: AiEvalError) -> ValidationFailed:
    return ValidationFailed("AIP eval request failed", details={"reason": exc.reason, "detail": exc.detail})
