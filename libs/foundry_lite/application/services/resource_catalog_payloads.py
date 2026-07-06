"""Payload and request helpers for resource catalog services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports.resource_catalog_repository import (
    PrincipalType,
    ProjectFolderRow,
    ProjectGrantRow,
    ProjectRole,
    ProjectRow,
    ResourceIdempotencyRow,
    ResourceRow,
)
from foundry_lite.application.primitives import _json_ready
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_PROJECT_ROLES = frozenset({"owner", "editor", "viewer", "discoverer"})
_PRINCIPAL_TYPES = frozenset({"user", "role", "group"})


def require_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValidationFailed(f"{field_name} is required", details={"field": field_name})
    return cleaned


def parse_project_role(value: str) -> ProjectRole:
    cleaned = require_text(value, "role")
    if cleaned not in _PROJECT_ROLES:
        raise ValidationFailed("invalid project role", details={"role": value})
    return cast(ProjectRole, cleaned)


def parse_principal_type(value: str) -> PrincipalType:
    cleaned = require_text(value, "principal_type")
    if cleaned not in _PRINCIPAL_TYPES:
        raise ValidationFailed("invalid project grant principal type", details={"principal_type": value})
    return cast(PrincipalType, cleaned)


def stable_request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def replay_or_conflict(row: ResourceIdempotencyRow, request_hash: str) -> dict[str, object]:
    if row["request_hash"] != request_hash:
        raise ConflictDetected(
            "idempotency key was reused with a different resource request",
            details={"scope": row["scope"], "idempotency_key": row["idempotency_key"]},
        )
    return dict(row["response_payload"])


def project_payload(row: ProjectRow, role: ProjectRole | None) -> dict[str, object]:
    return {
        "id": row["id"],
        "rid": row["rid"],
        "displayName": row["display_name"],
        "description": row["description"],
        "status": row["status"],
        "createdByUserId": row["created_by_user_id"],
        "metadata": dict(row["metadata"]),
        "role": role,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def grant_payload(row: ProjectGrantRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "principalType": row["principal_type"],
        "principalId": row["principal_id"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def folder_payload(row: ProjectFolderRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "rid": row["rid"],
        "projectId": row["project_id"],
        "parentFolderId": row["parent_folder_id"],
        "displayName": row["display_name"],
        "status": row["status"],
        "createdByUserId": row["created_by_user_id"],
        "metadata": dict(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def resource_payload(row: ResourceRow, *, is_favorite: bool = False) -> dict[str, object]:
    return {
        "id": row["id"],
        "rid": row["rid"],
        "resourceType": row["resource_type"],
        "displayName": row["display_name"],
        "projectId": row["project_id"],
        "folderId": row["folder_id"],
        "sourceSurface": row["source_surface"],
        "sourceRef": row["source_ref"],
        "operationsPath": row["operations_path"],
        "status": row["status"],
        "metadata": dict(row["metadata"]),
        "isFavorite": is_favorite,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
