"""Media set, transaction, processing, and reference routes."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import asdict
from typing import BinaryIO, cast

from fastapi import APIRouter, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.ports.media_storage import ByteRange
from foundry_lite.application.services.media.clearance import allowed_media_classifications
from foundry_lite.application.services.media.processing import ContentUnitPage
from foundry_lite.application.services.media.references import SourceMediaRead
from foundry_lite.application.upload_limits import max_media_upload_bytes
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    MEDIA_REFERENCE_ALLOWED_CLASSIFICATIONS_QUERY,
    MEDIA_UPLOAD_FILE,
    JsonObject,
    MediaBindReferenceRequest,
    MediaContentPromoteRequest,
    MediaIndexDerivativeRequest,
    MediaOpenTransactionRequest,
    MediaProcessRequest,
    MediaSearchRequest,
    MediaSetCreateRequest,
    MediaVisualPromoteRequest,
    MediaVisualSearchRequest,
)
from foundry_lite_api.serializers import _json_form_object, _optional_json_form_object
from foundry_lite_api.webhooks import _request_content_length

router = APIRouter()
_BYTE_RANGE_PATTERN = re.compile(r"^bytes=(\d+)-(\d*)$")
_MEDIA_STREAM_CHUNK = 1024 * 1024


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
        _reject_oversized_media_upload(request, file)
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


@router.get("/api/media/versions/{media_item_version_id}/content", response_model=None)
def read_media_version_content(
    request: Request,
    media_item_version_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
) -> Response:
    try:
        source = runtime.foundry.media.source_read(
            _ctx(request),
            media_item_version_id=media_item_version_id,
            byte_range=_parse_byte_range(range_header),
            should_proxy=True,
        )
        return _source_media_response(source)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.head("/api/media/versions/{media_item_version_id}/content", response_model=None)
def inspect_media_version_content(request: Request, media_item_version_id: str) -> Response:
    try:
        resolved = runtime.foundry.media.resolve_reference(
            _ctx(request),
            media_item_version_id=media_item_version_id,
        )
        version = resolved.version
        return Response(
            status_code=200,
            media_type=version.sniffed_mime_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(version.byte_size),
                "ETag": f'"{version.content_hash}"',
            },
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


@router.get("/api/media/derivatives/{media_derivative_id}/content-units")
def list_media_derivative_content_units(
    request: Request,
    media_derivative_id: str,
    after_ordinal: int | None = Query(default=None, alias="afterOrdinal"),
    page_number: int | None = Query(default=None, alias="pageNumber"),
    limit: int = Query(default=200),
) -> JsonObject:
    try:
        page = runtime.foundry.media.list_derivative_content_units(
            _ctx(request),
            media_derivative_id=media_derivative_id,
            after_ordinal=after_ordinal,
            page_number=page_number,
            limit=limit,
        )
        return _content_unit_page_payload(page)
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
        ctx = _ctx(request)
        # The classification allow-list is a SERVER decision from ctx roles; the client value is
        # never trusted for the security decision (omitting it must not grant full clearance).
        query = HybridContentQuery(
            tenant_id=ctx.tenant_id,
            text=payload.text,
            top_k=payload.top_k,
            allowed_classifications=allowed_media_classifications(ctx),
        )
        return [cast(JsonObject, asdict(hit)) for hit in runtime.foundry.media.search_content(ctx, query=query)]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/media/content/generations/promote")
def promote_media_content_generation(request: Request, payload: MediaContentPromoteRequest) -> JsonObject:
    """Make one indexed text generation the active query source.

    Indexing writes into a shadow generation; nothing reaches search until this compare-and-swap
    lands. That is the same boundary Palantir draws when an object type only becomes queryable
    once its indexing job reports complete — a half-built generation is never served.
    """
    try:
        runtime.foundry.media.promote_content_generation(
            _ctx(request),
            expected_active=payload.expected_active,
            generation=payload.generation,
        )
        return {"status": "ok", "generation": payload.generation}
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
        ctx = _ctx(request)
        # Mask by the caller's server-derived clearance, not the client-supplied query param: an
        # omitted value must still mask restricted media, never fall back to full clearance.
        del allowed_classifications
        resolved = runtime.foundry.media.resolve_bound_reference(
            ctx,
            holder_type=holder_type,
            holder_id=holder_id,
            property_name=property_name,
            allowed_classifications=allowed_media_classifications(ctx),
        )
        return cast(JsonObject, asdict(resolved)) if resolved is not None else None
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _reject_oversized_media_upload(request: Request, file: UploadFile) -> None:
    # Bound the upload before it is staged: reject on the declared Content-Length (mirrors the
    # webhook guard) and on the actual buffered byte size (in case the header is absent or lies).
    max_bytes = max_media_upload_bytes()
    content_length = _request_content_length(request)
    if content_length is not None and content_length > max_bytes:
        raise _media_upload_too_large(content_length, max_bytes)
    size_bytes = _uploaded_file_size(file)
    if size_bytes > max_bytes:
        raise _media_upload_too_large(size_bytes, max_bytes)


def _uploaded_file_size(file: UploadFile) -> int:
    handle = file.file
    handle.seek(0, os.SEEK_END)
    size_bytes = handle.tell()
    handle.seek(0)
    return size_bytes


def _media_upload_too_large(size_bytes: int, max_bytes: int) -> ValidationFailed:
    return ValidationFailed(
        "media upload exceeds configured size limit",
        details={"size_bytes": size_bytes, "max_bytes": max_bytes},
    )


def _parse_byte_range(value: str | None) -> ByteRange | None:
    if value is None:
        return None
    match = _BYTE_RANGE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValidationFailed("media Range header must contain one explicit byte range")
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else None
    return ByteRange(start, end)


def _source_media_response(source: SourceMediaRead) -> StreamingResponse | RedirectResponse:
    if source.grant.url is not None:
        headers = _source_headers(source, include_length=False)
        return RedirectResponse(source.grant.url, status_code=307, headers=headers)
    if source.grant.stream is None:
        raise ValidationFailed("media read grant did not contain a stream or URL")
    status_code = 206 if source.byte_range is not None else 200
    return StreamingResponse(
        _bounded_stream(source.grant.stream, _response_byte_size(source)),
        status_code=status_code,
        media_type=source.resolved.version.sniffed_mime_type,
        headers=_source_headers(source, include_length=True),
    )


def _source_headers(source: SourceMediaRead, *, include_length: bool) -> dict[str, str]:
    version = source.resolved.version
    headers = {"Accept-Ranges": "bytes", "ETag": f'"{version.content_hash}"'}
    if include_length:
        headers["Content-Length"] = str(_response_byte_size(source))
    if include_length and source.byte_range is not None:
        headers["Content-Range"] = f"bytes {source.byte_range.start}-{source.byte_range.end}/{version.byte_size}"
    return headers


def _response_byte_size(source: SourceMediaRead) -> int:
    if source.byte_range is None:
        return source.resolved.version.byte_size
    assert source.byte_range.end is not None
    return source.byte_range.end - source.byte_range.start + 1


def _bounded_stream(stream: BinaryIO, byte_size: int) -> Iterator[bytes]:
    remaining = byte_size
    try:
        while remaining > 0:
            chunk = stream.read(min(_MEDIA_STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        stream.close()


def _content_unit_page_payload(page: ContentUnitPage) -> JsonObject:
    return {
        "mediaDerivativeId": page.media_derivative_id,
        "sourceMediaItemVersionId": page.source_media_item_version_id,
        "items": [_content_unit_payload(unit) for unit in page.items],
        "nextCursor": page.next_cursor,
    }


def _content_unit_payload(unit: ContentUnitRecord) -> JsonObject:
    return {
        "id": unit.content_unit_id,
        "sourceMediaItemVersionId": unit.source_media_item_version_id,
        "mediaDerivativeId": unit.derivative_id,
        "unitKind": unit.unit_kind,
        "ordinal": unit.ordinal,
        "text": unit.text,
        "textHash": unit.text_hash,
        "pageNumber": unit.page_number,
        "startMs": unit.start_ms,
        "endMs": unit.end_ms,
        "timecode": _timecode_payload(unit),
        "bbox": unit.bbox,
        "parentContentUnitId": unit.parent_content_unit_id,
        "sourceLocator": unit.source_locator,
        "structure": unit.structure,
        "confidence": unit.confidence,
        "speaker": unit.speaker,
        "language": unit.language,
        "securityEnvelope": unit.security_envelope,
        "hasEmbedding": bool(unit.embedding),
        "createdAt": unit.created_at,
    }


def _timecode_payload(unit: ContentUnitRecord) -> JsonObject | None:
    if unit.start_ms is None and unit.end_ms is None:
        return None
    return {"startMs": unit.start_ms, "endMs": unit.end_ms}


def _processor_spec(payload: MediaProcessRequest) -> ProcessorSpec:
    return ProcessorSpec(
        processor=payload.processor,
        processor_version=payload.processor_version,
        model=payload.model,
        model_version=payload.model_version,
        parameters=payload.parameters,
    )
