"""AIP builder, agent, eval, and release routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Header, Request
from foundry_lite.application.services.aip.citation_service import CitationServiceError
from foundry_lite.application.services.aip.eval_types import AiEvalError, EvalCaseInput
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    AipAgentRunRequest,
    AipBuilderRunRequest,
    AipBuilderValidateRequest,
    AipCitationNavigationResolveRequest,
    AipEvalCaseRequest,
    AipEvalRunRequest,
    AipFdeRunRequest,
    AipPilotGenerateRequest,
    AipPilotPlanRequest,
    AipReleasePromotionRequest,
    JsonObject,
)

router = APIRouter()


@router.get("/api/aip/fde/catalog")
def get_aip_fde_catalog(request: Request) -> JsonObject:
    try:
        return cast(JsonObject, runtime.foundry.aip.fde_catalog(ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/fde/run")
def run_aip_fde(request: Request, payload: AipFdeRunRequest) -> JsonObject:
    try:
        result = runtime.foundry.aip.run_fde_payload(
            payload=payload.model_dump(by_alias=True),
            ctx=_ctx(request),
        )
        return result.to_payload()
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/fde/mcp/{application_id}/confirmations/{challenge_id}/approve")
def approve_aip_fde_mcp_confirmation(
    request: Request,
    application_id: str,
    challenge_id: str,
) -> JsonObject:
    """Let an authenticated human approve one fingerprint-bound Builder MCP mutation."""

    try:
        return cast(
            JsonObject,
            runtime.foundry.aip.approve_fde_mcp_confirmation(
                application_id,
                challenge_id,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/pilot/plan")
def plan_aip_pilot(request: Request, payload: AipPilotPlanRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.aip.plan_pilot_application(
                payload.model_dump(by_alias=True, exclude_none=True), ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/pilot/applications")
def generate_aip_pilot(
    request: Request,
    payload: AipPilotGenerateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.aip.generate_pilot_application(
                payload.plan, idempotency_key=idempotency_key, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/aip/pilot/applications/{rid}")
def get_aip_pilot(request: Request, rid: str) -> JsonObject:
    try:
        return cast(JsonObject, runtime.foundry.aip.get_pilot_application(rid, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/builder/validate")
def validate_aip_builder(request: Request, payload: AipBuilderValidateRequest) -> JsonObject:
    try:
        result = runtime.foundry.aip.validate_builder_payload(
            payload=payload.model_dump(by_alias=True),
            ctx=_ctx(request),
        )
        return result.to_payload()
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/builder/run")
def run_aip_builder(request: Request, payload: AipBuilderRunRequest) -> JsonObject:
    try:
        result = runtime.foundry.aip.run_builder_payload(
            payload=payload.model_dump(by_alias=True),
            ctx=_ctx(request),
        )
        return result.to_payload()
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/agent/run")
def run_aip_agent(request: Request, payload: AipAgentRunRequest) -> JsonObject:
    try:
        result = runtime.foundry.aip.run_agent_payload(
            payload=payload.model_dump(by_alias=True),
            ctx=_ctx(request),
        )
        return result.to_payload()
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/aip/citations/navigation/resolve")
def resolve_aip_citation_navigation(
    request: Request,
    payload: AipCitationNavigationResolveRequest,
) -> JsonObject:
    try:
        result = runtime.foundry.aip.resolve_citation_navigation(
            navigation_ref=payload.navigation_ref,
            ctx=_ctx(request),
        )
        return result.to_payload()
    except CitationServiceError as exc:
        denied = PermissionDenied(
            "citation navigation could not be verified",
            details={"reason": exc.reason},
        )
        raise _handle_error(denied, request) from exc


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
    return ValidationFailed(
        "AIP eval request failed",
        details={"reason": scrub_error_text(exc.reason), "detail": scrub_error_text(exc.detail)},
    )
