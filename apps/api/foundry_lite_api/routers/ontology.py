"""Ontology catalog and validation routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from foundry_lite.application.ports import OntologyCatalogResult, OntologyValidationResult
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import OntologyValidateRequest

router = APIRouter()


@router.get("/api/ontology/catalog")
def ontology_catalog(request: Request) -> OntologyCatalogResult:
    try:
        return runtime.foundry.ontology.catalog(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/ontology/validate")
def validate_ontology(request: Request, payload: OntologyValidateRequest) -> OntologyValidationResult:
    try:
        return runtime.foundry.ontology.validate(payload.yaml_text, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
