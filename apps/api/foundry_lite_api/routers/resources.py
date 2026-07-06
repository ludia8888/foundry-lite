"""Project, folder, and resource catalog routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    JsonObject,
    ProjectCreateRequest,
    ProjectFolderCreateRequest,
    ProjectFolderMoveRequest,
    ProjectGrantUpsertRequest,
    ResourceMoveRequest,
    ResourceReconcileRequest,
    ResourceRegisterRequest,
)

router = APIRouter()


@router.get("/api/projects")
def list_projects(request: Request) -> JsonObject:
    try:
        return runtime.foundry.resources.list_projects(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/projects")
def create_project(
    request: Request,
    payload: ProjectCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.create_project(
            display_name=payload.display_name,
            description=payload.description,
            metadata=payload.metadata,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/projects/{project_id}")
def get_project(request: Request, project_id: str) -> JsonObject:
    try:
        return runtime.foundry.resources.get_project(project_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/projects/{project_id}/grants")
def list_project_grants(request: Request, project_id: str) -> JsonObject:
    try:
        return runtime.foundry.resources.list_project_grants(project_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/projects/{project_id}/grants/{principal_type}/{principal_id}")
def upsert_project_grant(
    request: Request,
    project_id: str,
    principal_type: str,
    principal_id: str,
    payload: ProjectGrantUpsertRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.upsert_project_grant(
            project_id,
            principal_type=principal_type,
            principal_id=principal_id,
            role=payload.role,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/projects/{project_id}/folders")
def list_project_folders(
    request: Request,
    project_id: str,
    include_trashed: bool = Query(default=False, alias="includeTrashed"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.list_folders(project_id, include_trashed=include_trashed, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/projects/{project_id}/folders")
def create_project_folder(
    request: Request,
    project_id: str,
    payload: ProjectFolderCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.create_folder(
            project_id,
            display_name=payload.display_name,
            parent_folder_id=payload.parent_folder_id,
            metadata=payload.metadata,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/projects/{project_id}/folders/{folder_id}/move")
def move_project_folder(
    request: Request,
    project_id: str,
    folder_id: str,
    payload: ProjectFolderMoveRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.move_folder(
            project_id,
            folder_id,
            parent_folder_id=payload.parent_folder_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/projects/{project_id}/folders/{folder_id}/trash")
def trash_project_folder(
    request: Request,
    project_id: str,
    folder_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.trash_folder(
            project_id, folder_id, idempotency_key=idempotency_key, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/projects/{project_id}/folders/{folder_id}/restore")
def restore_project_folder(
    request: Request,
    project_id: str,
    folder_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.restore_folder(
            project_id, folder_id, idempotency_key=idempotency_key, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/resources")
def list_resources(
    request: Request,
    project_id: str | None = Query(default=None, alias="projectId"),
    folder_id: str | None = Query(default=None, alias="folderId"),
    include_trashed: bool = Query(default=False, alias="includeTrashed"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.list_resources(
            project_id=project_id,
            folder_id=folder_id,
            include_trashed=include_trashed,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/resources/register")
def register_resource(
    request: Request,
    payload: ResourceRegisterRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.register_resource(
            resource_type=payload.resource_type,
            display_name=payload.display_name,
            project_id=payload.project_id,
            folder_id=payload.folder_id,
            source_surface=payload.source_surface,
            source_ref=payload.source_ref,
            operations_path=payload.operations_path,
            metadata=payload.metadata,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/resources/reconcile")
def reconcile_resources(
    request: Request,
    payload: ResourceReconcileRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.reconcile(
            project_id=payload.project_id, idempotency_key=idempotency_key, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/resources/{rid}")
def get_resource(request: Request, rid: str) -> JsonObject:
    try:
        return runtime.foundry.resources.get_resource(rid, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/resources/{rid}/move")
def move_resource(
    request: Request,
    rid: str,
    payload: ResourceMoveRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.resources.move_resource(
            rid,
            project_id=payload.project_id,
            folder_id=payload.folder_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/resources/{rid}/trash")
def trash_resource(request: Request, rid: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> JsonObject:
    try:
        return runtime.foundry.resources.trash_resource(rid, idempotency_key=idempotency_key, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/resources/{rid}/restore")
def restore_resource(request: Request, rid: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> JsonObject:
    try:
        return runtime.foundry.resources.restore_resource(rid, idempotency_key=idempotency_key, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/resources/{rid}/favorite")
def favorite_resource(request: Request, rid: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> JsonObject:
    try:
        return runtime.foundry.resources.favorite_resource(rid, idempotency_key=idempotency_key, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.delete("/api/resources/{rid}/favorite")
def unfavorite_resource(
    request: Request, rid: str, idempotency_key: str = Header(alias="Idempotency-Key")
) -> JsonObject:
    try:
        return runtime.foundry.resources.unfavorite_resource(rid, idempotency_key=idempotency_key, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
