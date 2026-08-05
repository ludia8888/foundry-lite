"""Ontology catalog, validation, apply, rollback, and resource insight routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from foundry_lite.application.ports import OntologyCatalogResult, OntologyValidationResult
from foundry_lite.application.services.ontology_insights import (
    DEFAULT_USAGE_WINDOW_DAYS,
    MAX_USAGE_WINDOW_DAYS,
    OntologyResourceDependentsResult,
    OntologyResourceType,
    OntologyResourceUsageResult,
)
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    JsonObject,
    OntologyApplyRequest,
    OntologyBranchActionTypeDeleteRequest,
    OntologyBranchActionTypeRequest,
    OntologyBranchCreateRequest,
    OntologyBranchProposeRequest,
    OntologyBranchRebaseRequest,
    OntologyBranchUpdateRequest,
    OntologyProposalAssignRequest,
    OntologyProposalDecisionRequest,
    OntologyProposalExecuteRequest,
    OntologyProposalSubmitRequest,
    OntologyProposalUpdateRequest,
    OntologyProposalWithdrawRequest,
    OntologyRollbackRequest,
    OntologyValidateRequest,
)

router = APIRouter()


@router.get("/api/ontology/catalog")
def ontology_catalog(request: Request) -> OntologyCatalogResult:
    try:
        return runtime.foundry.ontology.catalog(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/resources/{resource_type}/{api_name}/usage")
def ontology_resource_usage(
    request: Request,
    resource_type: OntologyResourceType,
    api_name: str,
    window_days: int = Query(default=DEFAULT_USAGE_WINDOW_DAYS, ge=1, le=MAX_USAGE_WINDOW_DAYS, alias="windowDays"),
) -> OntologyResourceUsageResult:
    try:
        return runtime.foundry.ontology.resource_usage(
            resource_type,
            api_name,
            window_days=window_days,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/resources/{resource_type}/{api_name}/dependents")
def ontology_resource_dependents(
    request: Request,
    resource_type: OntologyResourceType,
    api_name: str,
) -> OntologyResourceDependentsResult:
    try:
        return runtime.foundry.ontology.resource_dependents(resource_type, api_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/validate")
def validate_ontology(request: Request, payload: OntologyValidateRequest) -> OntologyValidationResult:
    try:
        return runtime.foundry.ontology.validate(payload.yaml_text, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/apply")
def apply_ontology(request: Request, payload: OntologyApplyRequest) -> JsonObject:
    try:
        result = runtime.foundry.ontology.apply_text(payload.yaml_text, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {
        "ontologyVersionId": result["ontology_version_id"],
        "versionNumber": result["version_number"],
        "migrationPlan": result["migration_plan"],
    }


@router.post("/api/ontology/rollback")
def rollback_ontology(request: Request, payload: OntologyRollbackRequest) -> JsonObject:
    try:
        result = runtime.foundry.ontology.rollback(payload.version_number, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {
        "ontologyVersionId": result["ontology_version_id"],
        "versionNumber": result["version_number"],
        "rolledBackFromVersionNumber": result["rolled_back_from_version_number"],
        "rolledBackToVersionNumber": result["rolled_back_to_version_number"],
        "migrationPlan": result["migration_plan"],
    }


@router.get("/api/ontology/proposals")
def list_ontology_proposals(
    request: Request,
    status: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.list_proposals(status=status, cursor=cursor, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/proposals/{proposal_id}")
def get_ontology_proposal(request: Request, proposal_id: str) -> JsonObject:
    try:
        return runtime.foundry.ontology.get_proposal(proposal_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/proposals")
def submit_ontology_proposal(
    request: Request,
    payload: OntologyProposalSubmitRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.submit_proposal(
            yaml_text=payload.yaml_text,
            title=payload.title,
            description=payload.description,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/proposals/{proposal_id}/assign")
def assign_ontology_proposal(
    request: Request,
    proposal_id: str,
    payload: OntologyProposalAssignRequest,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.assign_proposal(
            proposal_id,
            reviewer_user_id=payload.reviewer_user_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/proposals/{proposal_id}/update")
def update_ontology_proposal(
    request: Request,
    proposal_id: str,
    payload: OntologyProposalUpdateRequest,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.update_proposal(
            proposal_id,
            yaml_text=payload.yaml_text,
            expected_fingerprint=payload.expected_fingerprint,
            title=payload.title,
            description=payload.description,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/proposals/{proposal_id}/decide")
def decide_ontology_proposal(
    request: Request,
    proposal_id: str,
    payload: OntologyProposalDecisionRequest,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.decide_proposal(
            proposal_id,
            decision=payload.decision,
            expected_fingerprint=payload.expected_fingerprint,
            comment=payload.comment,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/proposals/{proposal_id}/execute")
def execute_ontology_proposal(
    request: Request,
    proposal_id: str,
    payload: OntologyProposalExecuteRequest,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=payload.expected_fingerprint,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/proposals/{proposal_id}/withdraw")
def withdraw_ontology_proposal(
    request: Request,
    proposal_id: str,
    payload: OntologyProposalWithdrawRequest | None = None,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.withdraw_proposal(
            proposal_id,
            reason=payload.reason if payload is not None else None,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/branches")
def create_ontology_branch(
    request: Request,
    payload: OntologyBranchCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.create_branch(
            name=payload.name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/branches")
def list_ontology_branches(
    request: Request,
    status: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.list_branches(status=status, cursor=cursor, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/branches/{branch_id}")
def get_ontology_branch(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.ontology.get_branch(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/branches/{branch_id}/action-types")
def list_ontology_branch_action_types(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.ontology.list_branch_action_types(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/branches/{branch_id}/action-types/{action_api_name}")
def get_ontology_branch_action_type(request: Request, branch_id: str, action_api_name: str) -> JsonObject:
    try:
        return runtime.foundry.ontology.get_branch_action_type(branch_id, action_api_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/branches/{branch_id}/action-types")
def create_ontology_branch_action_type(
    request: Request,
    branch_id: str,
    payload: OntologyBranchActionTypeRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.create_branch_action_type(
            branch_id,
            definition=payload.definition,
            expected_fingerprint=payload.expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/ontology/branches/{branch_id}/action-types/{action_api_name}")
def update_ontology_branch_action_type(
    request: Request,
    branch_id: str,
    action_api_name: str,
    payload: OntologyBranchActionTypeRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.update_branch_action_type(
            branch_id,
            action_api_name,
            definition=payload.definition,
            expected_fingerprint=payload.expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.delete("/api/ontology/branches/{branch_id}/action-types/{action_api_name}")
def delete_ontology_branch_action_type(
    request: Request,
    branch_id: str,
    action_api_name: str,
    payload: OntologyBranchActionTypeDeleteRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.delete_branch_action_type(
            branch_id,
            action_api_name,
            expected_fingerprint=payload.expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/branches/{branch_id}/update")
def update_ontology_branch(
    request: Request,
    branch_id: str,
    payload: OntologyBranchUpdateRequest,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.update_branch(
            branch_id,
            yaml_text=payload.yaml_text,
            expected_fingerprint=payload.expected_fingerprint,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/ontology/branches/{branch_id}/diff")
def diff_ontology_branch(request: Request, branch_id: str) -> JsonObject:
    try:
        return runtime.foundry.ontology.branch_diff(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/branches/{branch_id}/rebase")
def rebase_ontology_branch(
    request: Request,
    branch_id: str,
    payload: OntologyBranchRebaseRequest,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.rebase_branch(
            branch_id,
            expected_fingerprint=payload.expected_fingerprint,
            resolutions=[item.model_dump(by_alias=True) for item in payload.resolutions],
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/branches/{branch_id}/propose")
def propose_ontology_branch(
    request: Request,
    branch_id: str,
    payload: OntologyBranchProposeRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.ontology.propose_branch(
            branch_id,
            title=payload.title,
            description=payload.description,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/branches/{branch_id}/abandon")
def abandon_ontology_branch(
    request: Request,
    branch_id: str,
) -> JsonObject:
    try:
        return runtime.foundry.ontology.abandon_branch(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
