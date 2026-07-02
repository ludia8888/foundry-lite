"""Source onboarding, managed sync, upload, and webhook listener routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from foundry_lite.application.services.source_onboarding_config import SourceUpload
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from pydantic import ValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    MEDIA_UPLOAD_FILE,
    SOURCE_BATCH_FILES,
    JsonObject,
    SourceAgentRegisterRequest,
    SourceBatchFileManifest,
    SourceCredentialCreateRequest,
    SourceDebeziumCreateRequest,
    SourceDebeziumSyncStartRequest,
    SourceExploreRequest,
    SourceManagedSyncCreateRequest,
    SourceManagedSyncRunStartRequest,
    SourceNetworkPolicyCreateRequest,
    SourceSchedulerTickRequest,
    SourceWebhookListenerCreateRequest,
)
from foundry_lite_api.serializers import _json_form_object, _json_form_string_list, _optional_json_form_object
from foundry_lite_api.webhooks import (
    WEBHOOK_SERVICE_PRINCIPAL_HEADER,
    WEBHOOK_SERVICE_TENANT_HEADER,
    _bounded_webhook_body,
    _webhook_payload,
    _webhook_payload_request,
    _webhook_request_context,
)

router = APIRouter()


@router.get("/api/sources")
def list_sources(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_sources(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/templates")
def list_source_templates(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_templates(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/credentials")
def create_source_credential(
    request: Request,
    payload: SourceCredentialCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.create_credential(
            credential_name=payload.credential_name,
            display_name=payload.display_name,
            kind=payload.kind,
            auth_scheme=payload.auth_scheme,
            secret_value=payload.secret_value,
            secret_name=payload.secret_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/credentials")
def list_source_credentials(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_credentials(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/credentials/{credential_name}")
def get_source_credential(request: Request, credential_name: str) -> JsonObject:
    try:
        return runtime.foundry.sources.get_credential(credential_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/agents")
def register_source_agent(
    request: Request,
    payload: SourceAgentRegisterRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.register_agent(
            agent_id=payload.agent_id,
            display_name=payload.display_name,
            mode=payload.mode,
            capabilities=payload.capabilities,
            network_summary=payload.network_summary,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/agents")
def list_source_agents(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_agents(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/agents/{agent_id}/heartbeat")
def heartbeat_source_agent(request: Request, agent_id: str) -> JsonObject:
    try:
        return runtime.foundry.sources.heartbeat_agent(agent_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/network-policies")
def create_source_network_policy(
    request: Request,
    payload: SourceNetworkPolicyCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.create_network_policy(
            policy_name=payload.policy_name,
            display_name=payload.display_name,
            mode=payload.mode,
            agent_id=payload.agent_id,
            allowed_hosts=payload.allowed_hosts,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/network-policies")
def list_source_network_policies(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_network_policies(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/explore")
def explore_source(request: Request, payload: SourceExploreRequest) -> JsonObject:
    try:
        return runtime.foundry.sources.explore_source(
            source_name=payload.source_name,
            source_type=payload.source_type,
            request=payload.request,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/managed-syncs")
def create_source_managed_sync(
    request: Request,
    payload: SourceManagedSyncCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.create_managed_sync(
            sync_name=payload.sync_name,
            source_name=payload.source_name,
            display_name=payload.display_name,
            source_type=payload.source_type,
            capability=payload.capability,
            mode=payload.mode,
            target_dataset_ref=payload.target_dataset_ref,
            target_media_set_id=payload.target_media_set_id,
            schedule=payload.schedule,
            config_summary=payload.config_summary,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/managed-syncs")
def list_source_managed_syncs(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_managed_syncs(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/managed-syncs/{sync_name}")
def get_source_managed_sync(request: Request, sync_name: str) -> JsonObject:
    try:
        return runtime.foundry.sources.get_managed_sync(sync_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/managed-syncs/{sync_name}/runs/start")
def start_source_managed_sync_run(
    request: Request,
    sync_name: str,
    payload: SourceManagedSyncRunStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.start_managed_sync_run(
            sync_name,
            trigger_type=payload.trigger_type,
            batch_limit=payload.batch_limit,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/managed-syncs/{sync_name}/runs")
def list_source_managed_sync_runs(request: Request, sync_name: str) -> list[JsonObject]:
    try:
        return runtime.foundry.sources.list_managed_sync_runs(sync_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/managed-sync-runs/{run_id}")
def get_source_managed_sync_run(request: Request, run_id: str) -> JsonObject:
    try:
        return runtime.foundry.sources.get_managed_sync_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/scheduler/due")
def preview_source_scheduler_due(
    request: Request,
    max_runs: int = Query(default=50, ge=1, le=500, alias="maxRuns"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.preview_due_managed_syncs(ctx=_ctx(request), max_runs=max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/scheduler/tick")
def run_source_scheduler_tick(request: Request, payload: SourceSchedulerTickRequest) -> JsonObject:
    try:
        return runtime.foundry.sources.run_due_managed_syncs(ctx=_ctx(request), max_runs=payload.max_runs)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/csv/uploads")
def upload_csv_source(
    request: Request,
    source_name: str = Form(..., alias="sourceName"),
    display_name: str = Form(..., alias="displayName"),
    dataset_ref: str = Form(..., alias="datasetRef"),
    sync_name: str | None = Form(default=None, alias="syncName"),
    primary_key: str = Form(default="[]", alias="primaryKey"),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        file.file.seek(0)
        return runtime.foundry.sources.upload_csv(
            source_name=source_name,
            display_name=display_name,
            dataset_ref=dataset_ref,
            file_name=file.filename or "upload.csv",
            source=file.file,
            sync_name=sync_name,
            primary_key=_json_form_string_list(primary_key, "primaryKey"),
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/batch-files/uploads")
def upload_batch_file_source(
    request: Request,
    manifest: str = Form(...),
    files: list[UploadFile] = SOURCE_BATCH_FILES,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        parsed = _source_batch_manifest(manifest)
        uploads = _source_batch_uploads(parsed, files)
        return runtime.foundry.sources.upload_batch_files(
            source_name=parsed.source_name,
            display_name=parsed.display_name,
            uploads=uploads,
            sync_name=parsed.sync_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/webhook-listeners")
def create_webhook_listener_source(
    request: Request,
    payload: SourceWebhookListenerCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        inbound_url = str(
            request.url_for(
                "ingest_source_webhook_listener_event",
                source_name=payload.source_name,
            )
        )
        return runtime.foundry.sources.create_webhook_listener(
            source_name=payload.source_name,
            display_name=payload.display_name,
            dataset_ref=payload.dataset_ref,
            connector_name=payload.connector_name,
            resource_name=payload.resource_name,
            signing_secret_ref=payload.signing_secret_ref,
            inbound_url=inbound_url,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/webhook-listeners/{source_name}")
def get_webhook_listener_source(request: Request, source_name: str) -> JsonObject:
    try:
        source = runtime.foundry.sources.get_source(source_name, ctx=_ctx(request))
        if source.get("kind") != "webhook_listener":
            raise ValidationFailed("source is not a webhook listener", details={"sourceName": source_name})
        return source
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/webhook-listeners/{source_name}/events")
async def ingest_source_webhook_listener_event(
    request: Request,
    source_name: str,
    signature: str = Header(alias="X-Foundry-Lite-Signature"),
    signature_timestamp: str = Header(alias="X-Foundry-Lite-Timestamp"),
    event_id: str | None = Header(default=None, alias="X-Foundry-Lite-Event-ID"),
    service_principal: str | None = Header(default=None, alias=WEBHOOK_SERVICE_PRINCIPAL_HEADER),
    service_tenant_id: str | None = Header(default=None, alias=WEBHOOK_SERVICE_TENANT_HEADER),
) -> JsonObject:
    try:
        raw_body = await _bounded_webhook_body(request)
        payload = _webhook_payload_request(raw_body)
        webhook_ctx = _webhook_request_context(
            request,
            service_principal=service_principal,
            service_tenant_id=service_tenant_id,
        )
        return runtime.foundry.sources.ingest_webhook_listener_event(
            source_name,
            payload=_webhook_payload(payload),
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            event_id=event_id,
            require_service_principal_signature=webhook_ctx.require_service_principal_signature,
            ctx=webhook_ctx.ctx,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/cdc/debezium")
def create_debezium_source(
    request: Request,
    payload: SourceDebeziumCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.create_debezium_source(
            source_name=payload.source_name,
            display_name=payload.display_name,
            dataset_ref=payload.dataset_ref,
            stream_name=payload.stream_name,
            topic=payload.topic,
            consumer_group=payload.consumer_group,
            secret_refs=payload.secret_refs,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/cdc/debezium/{source_name}/sync/start")
def start_debezium_source_sync(
    request: Request,
    source_name: str,
    payload: SourceDebeziumSyncStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.sources.start_debezium_sync(
            source_name,
            expected_config_fingerprint=payload.expected_config_fingerprint,
            after_offset=payload.after_offset,
            limit=payload.limit,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/sources/media/uploads")
def upload_media_source(
    request: Request,
    source_name: str = Form(..., alias="sourceName"),
    display_name: str = Form(..., alias="displayName"),
    media_set_id: str = Form(..., alias="mediaSetId"),
    logical_path: str = Form(..., alias="logicalPath"),
    schema_type: str = Form(..., alias="schemaType"),
    format: str = Form(...),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    supplied_mime_type: str | None = Form(default=None, alias="suppliedMimeType"),
    security_envelope: str = Form(default="{}", alias="securityEnvelope"),
    probe_metadata: str | None = Form(default=None, alias="probeMetadata"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        file.file.seek(0)
        return runtime.foundry.sources.upload_media(
            source_name=source_name,
            display_name=display_name,
            media_set_id=media_set_id,
            logical_path=logical_path,
            file_name=file.filename or logical_path,
            source=file.file,
            supplied_mime_type=supplied_mime_type or file.content_type or "application/octet-stream",
            schema_type=schema_type,
            format=format,
            security_envelope=_json_form_object(security_envelope, "securityEnvelope"),
            probe_metadata=_optional_json_form_object(probe_metadata, "probeMetadata"),
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/sources/{source_name}")
def get_source(request: Request, source_name: str) -> JsonObject:
    try:
        return runtime.foundry.sources.get_source(source_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _source_batch_manifest(value: str) -> SourceBatchFileManifest:
    try:
        return SourceBatchFileManifest.model_validate_json(value)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _source_batch_uploads(manifest: SourceBatchFileManifest, files: list[UploadFile]) -> list[SourceUpload]:
    by_name = {file.filename or f"file-{index}": file for index, file in enumerate(files)}
    uploads: list[SourceUpload] = []
    for item in manifest.files:
        upload = by_name.get(item.file_name)
        if upload is None:
            raise ValidationFailed(
                "batch file manifest references a missing upload", details={"fileName": item.file_name}
            )
        upload.file.seek(0)
        uploads.append(SourceUpload(file_name=item.file_name, dataset_ref=item.dataset_ref, source=upload.file))
    return uploads
