"""Ontology catalog, validation, apply, rollback, and resource insight routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
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
