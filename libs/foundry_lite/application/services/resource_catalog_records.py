"""Record factories for resource catalog mutations."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from foundry_lite.application.ports import AuditEventRecord, OutboxEventRecord
from foundry_lite.application.ports.resource_catalog_repository import (
    PrincipalType,
    ProjectFolderRecord,
    ProjectGrantRecord,
    ProjectRecord,
    ProjectRole,
    ResourceRecord,
)
from foundry_lite.domain.context import RequestContext


def project_record(
    ctx: RequestContext,
    *,
    display_name: str,
    description: str | None,
    metadata: Mapping[str, object],
    now: str,
) -> ProjectRecord:
    project_id = f"project_{uuid4().hex}"
    return ProjectRecord(
        project_id=project_id,
        tenant_id=ctx.tenant_id,
        rid=_rid("project"),
        display_name=display_name,
        description=description,
        status="active",
        created_by_user_id=ctx.actor_user_id,
        metadata=dict(metadata),
        created_at=now,
        updated_at=now,
    )


def owner_grant_record(ctx: RequestContext, project_id: str, *, now: str) -> ProjectGrantRecord:
    return grant_record(
        ctx,
        project_id=project_id,
        principal_type="user",
        principal_id=ctx.actor_user_id,
        role="owner",
        now=now,
    )


def grant_record(
    ctx: RequestContext,
    *,
    project_id: str,
    principal_type: PrincipalType,
    principal_id: str,
    role: ProjectRole,
    now: str,
) -> ProjectGrantRecord:
    return ProjectGrantRecord(
        grant_id=f"project_grant_{uuid4().hex}",
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        principal_type=principal_type,
        principal_id=principal_id,
        role=role,
        created_at=now,
        updated_at=now,
    )


def folder_record(
    ctx: RequestContext,
    *,
    project_id: str,
    parent_folder_id: str | None,
    display_name: str,
    metadata: Mapping[str, object],
    now: str,
) -> ProjectFolderRecord:
    return ProjectFolderRecord(
        folder_id=f"folder_{uuid4().hex}",
        tenant_id=ctx.tenant_id,
        rid=_rid("folder"),
        project_id=project_id,
        parent_folder_id=parent_folder_id,
        display_name=display_name,
        status="active",
        created_by_user_id=ctx.actor_user_id,
        metadata=dict(metadata),
        created_at=now,
        updated_at=now,
    )


def resource_record(
    ctx: RequestContext,
    *,
    resource_type: str,
    display_name: str,
    project_id: str,
    folder_id: str | None,
    source_surface: str,
    source_ref: str,
    operations_path: str | None,
    metadata: Mapping[str, object],
    now: str,
) -> ResourceRecord:
    return ResourceRecord(
        resource_id=f"resource_{uuid4().hex}",
        tenant_id=ctx.tenant_id,
        rid=_rid(resource_type),
        resource_type=resource_type,
        display_name=display_name,
        project_id=project_id,
        folder_id=folder_id,
        source_surface=source_surface,
        source_ref=source_ref,
        operations_path=operations_path,
        status="active",
        metadata=dict(metadata),
        created_at=now,
        updated_at=now,
    )


def audit_record(
    ctx: RequestContext,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str | None,
    action: str,
    after_ref: Mapping[str, object],
    now: str,
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=f"audit_{uuid4().hex}",
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        decision="allow",
        policy_decision={"source": "project_grant"},
        before_ref={},
        after_ref=dict(after_ref),
        correlation_id=ctx.request_id,
        request_id=ctx.request_id,
        metadata={},
        created_at=now,
    )


def outbox_record(
    ctx: RequestContext,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Mapping[str, object],
    idempotency_key: str,
    now: str,
) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=f"outbox_{uuid4().hex}",
        tenant_id=ctx.tenant_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=dict(payload),
        status="pending",
        attempts=0,
        idempotency_key=idempotency_key,
        correlation_id=ctx.request_id,
        created_at=now,
        published_at=None,
    )


def _rid(resource_type: str) -> str:
    safe_type = resource_type.strip().replace("_", "-") or "resource"
    return f"ri.foundry-lite.{safe_type}.{uuid4()}"
