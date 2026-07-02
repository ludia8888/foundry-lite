"""Media set, transaction, processing, and reference routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Form, Header, Query, Request, UploadFile
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY,
    MEDIA_UPLOAD_FILE,
    JsonObject,
    MediaBindReferenceRequest,
    MediaIndexDerivativeRequest,
    MediaOpenTransactionRequest,
    MediaProcessRequest,
    MediaSearchRequest,
    MediaSetCreateRequest,
    MediaVisualPromoteRequest,
    MediaVisualSearchRequest,
)
from foundry_lite_api.serializers import _json_form_object, _optional_json_form_object

router = APIRouter()


@router.post("/api/media/sets")
def create_media_set(request: Request, payload: MediaSetCreateRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                runtime.foundry.media.create_media_set(
                    _ctx(request),
                    namespace=payload.namespace,
                    name=payload.name,
                    schema_type=payload.schema_type,
                    primary_format=payload.primary_format,
                    allowed_input_formats=tuple(payload.allowed_input_formats),
                    classification=payload.classification,
                    transaction_policy=payload.transaction_policy,
                    storage_profile=payload.storage_profile,
                    processing_profile=payload.processing_profile,
                    retention_policy_id=payload.retention_policy_id,
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/media/sets/{media_set_id}")
def get_media_set(request: Request, media_set_id: str) -> JsonObject:
    try:
        return cast(JsonObject, asdict(runtime.foundry.media.get_media_set(_ctx(request), media_set_id=media_set_id)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/sets/{media_set_id}/transactions")
def open_media_transaction(
    request: Request,
    media_set_id: str,
    payload: MediaOpenTransactionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        media_transaction_id = runtime.foundry.media.open_transaction(
            _ctx(request), media_set_id=media_set_id, idempotency_key=idempotency_key, mode=payload.mode
        )
        return {"mediaTransactionId": media_transaction_id}
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/sets/{media_set_id}/transactions/{media_transaction_id}/uploads")
def upload_media_file(
    request: Request,
    media_set_id: str,
    media_transaction_id: str,
    logical_path: str = Form(..., alias="logicalPath"),
    schema_type: str = Form(..., alias="schemaType"),
    format: str = Form(...),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    supplied_mime_type: str | None = Form(default=None, alias="suppliedMimeType"),
    security_envelope: str = Form(default="{}", alias="securityEnvelope"),
    probe_metadata: str | None = Form(default=None, alias="probeMetadata"),
) -> JsonObject:
    try:
        file.file.seek(0)
        staged = runtime.foundry.media.upload(
            _ctx(request),
            media_set_id=media_set_id,
            media_transaction_id=media_transaction_id,
            logical_path=logical_path,
            source=file.file,
            supplied_mime_type=supplied_mime_type or file.content_type or "application/octet-stream",
            schema_type=schema_type,
            format=format,
            security_envelope=_json_form_object(security_envelope, "securityEnvelope"),
            probe_metadata=_optional_json_form_object(probe_metadata, "probeMetadata"),
        )
        return cast(JsonObject, asdict(staged))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/transactions/{media_transaction_id}/commit")
def commit_media_transaction(request: Request, media_transaction_id: str) -> JsonObject:
    try:
        return cast(
            JsonObject, asdict(runtime.foundry.media.commit(_ctx(request), media_transaction_id=media_transaction_id))
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/versions/{media_item_version_id}/process")
def process_media_version(request: Request, media_item_version_id: str, payload: MediaProcessRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                runtime.foundry.media.process(
                    _ctx(request), media_item_version_id=media_item_version_id, spec=_processor_spec(payload)
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/media/derivatives/{media_derivative_id}")
def get_media_derivative(request: Request, media_derivative_id: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(runtime.foundry.media.resolve_derivative(_ctx(request), media_derivative_id=media_derivative_id)),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/derivatives/{media_derivative_id}/index")
def index_media_derivative(
    request: Request, media_derivative_id: str, payload: MediaIndexDerivativeRequest
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                runtime.foundry.media.index_derivative(
                    _ctx(request), media_derivative_id=media_derivative_id, generation=payload.generation
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/content/search")
def search_media_content(request: Request, payload: MediaSearchRequest) -> list[JsonObject]:
    try:
        query = HybridContentQuery(
            tenant_id=_ctx(request).tenant_id,
            text=payload.text,
            top_k=payload.top_k,
            allowed_classifications=_optional_tuple(payload.allowed_classifications),
        )
        return [
            cast(JsonObject, asdict(hit)) for hit in runtime.foundry.media.search_content(_ctx(request), query=query)
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/visual/derivatives/{media_derivative_id}/index")
def index_media_visual_derivative(
    request: Request,
    media_derivative_id: str,
    payload: MediaIndexDerivativeRequest,
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                runtime.foundry.media.index_visual_derivative(
                    _ctx(request),
                    media_derivative_id=media_derivative_id,
                    generation=payload.generation,
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/visual/generations/promote")
def promote_media_visual_generation(request: Request, payload: MediaVisualPromoteRequest) -> JsonObject:
    try:
        runtime.foundry.media.promote_visual_generation(
            _ctx(request),
            expected_active=payload.expected_active,
            generation=payload.generation,
        )
        return {"status": "ok", "generation": payload.generation}
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/visual/search")
def search_media_visual(request: Request, payload: MediaVisualSearchRequest) -> list[JsonObject]:
    try:
        hits = runtime.foundry.media.search_visual(_ctx(request), text=payload.text, top_k=payload.top_k)
        return [cast(JsonObject, asdict(hit)) for hit in hits]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/media/processing-runs")
def list_media_processing_runs(
    request: Request,
    source_media_item_version_id: str | None = Query(default=None, alias="sourceMediaItemVersionId"),
    limit: int = Query(default=50),
) -> list[JsonObject]:
    try:
        runs = runtime.foundry.media.list_media_runs(
            _ctx(request), source_media_item_version_id=source_media_item_version_id, limit=limit
        )
        return [cast(JsonObject, asdict(run)) for run in runs]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/media/processing-runs/{media_processing_run_id}")
def get_media_processing_run(request: Request, media_processing_run_id: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                runtime.foundry.media.media_run_detail(_ctx(request), media_processing_run_id=media_processing_run_id)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/references/bind")
def bind_media_reference(
    request: Request,
    payload: MediaBindReferenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            asdict(
                runtime.foundry.media.bind_reference(
                    _ctx(request),
                    holder_type=payload.holder_type,
                    holder_id=payload.holder_id,
                    property_name=payload.property_name,
                    media_item_version_id=payload.media_item_version_id,
                    idempotency_key=idempotency_key,
                )
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/media/references/resolve")
def resolve_media_reference(
    request: Request,
    holder_type: str = Query(alias="holderType"),
    holder_id: str = Query(alias="holderId"),
    property_name: str = Query(alias="propertyName"),
    allowed_classifications: list[str] | None = MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY,
) -> JsonObject | None:
    try:
        resolved = runtime.foundry.media.resolve_bound_reference(
            _ctx(request),
            holder_type=holder_type,
            holder_id=holder_id,
            property_name=property_name,
            allowed_classifications=_optional_tuple(allowed_classifications),
        )
        return cast(JsonObject, asdict(resolved)) if resolved is not None else None
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _processor_spec(payload: MediaProcessRequest) -> ProcessorSpec:
    return ProcessorSpec(
        processor=payload.processor,
        processor_version=payload.processor_version,
        model=payload.model,
        model_version=payload.model_version,
        parameters=payload.parameters,
    )


def _optional_tuple(values: list[str] | None) -> tuple[str, ...] | None:
    return tuple(values) if values is not None else None
