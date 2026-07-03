"""Payload shapes and pure helpers for ontology branch workflows.

Branches snapshot main by rendering the ACTIVE persisted ontology version
through the same definition-snapshot black box the rollback path replays, so a
branch's base is byte-deterministic and always re-importable. The snapshot is
serialized with ``json.dumps`` (JSON is a strict subset of YAML 1.2), which the
ontology YAML loader accepts unchanged.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

from foundry_lite.application.ports import OntologyVersionRow, TransactionContext
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRecord, OntologyBranchRow
from foundry_lite.application.ports.ontology_repository import OntologyRepository
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.ontology_branch_diff import serialize_definition_yaml
from foundry_lite.application.services.ontology_definition_snapshot import (
    JsonObject,
    ontology_definition_snapshot,
)
from foundry_lite.application.services.ontology_proposal_payloads import yaml_fingerprint
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

BRANCH_STATUSES = ("open", "merged", "abandoned")


@dataclass(frozen=True)
class BranchPageCursor:
    """Keyset position after which the next branch page starts."""

    created_before: str | None
    before_id: str | None


@dataclass(frozen=True)
class ActiveOntologySnapshot:
    """The active version row plus its rendered YAML-equivalent definition."""

    version: OntologyVersionRow
    yaml_text: str


def required_text(value: str | None, field: str) -> str:
    if value is None or not value.strip():
        raise ValidationFailed(f"{field} is required", details={"field": field})
    return value.strip()


def branch_list_status_filter(status: str | None) -> str | None:
    if status is None:
        return None
    if status not in BRANCH_STATUSES:
        raise ValidationFailed(
            "invalid ontology branch status filter",
            details={"status": status, "allowed": list(BRANCH_STATUSES)},
        )
    return status


def bounded_branch_limit(limit: int) -> int:
    if limit < 1 or limit > 200:
        raise ValidationFailed("limit must be between 1 and 200", details={"limit": limit})
    return limit


def encode_branch_cursor(row: OntologyBranchRow) -> str:
    raw = json.dumps({"createdAt": row["created_at"], "id": row["id"]}, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_branch_cursor(cursor: str | None) -> BranchPageCursor:
    if cursor is None:
        return BranchPageCursor(None, None)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, binascii.Error) as exc:
        raise ValidationFailed("invalid ontology branch cursor", details={"cursor": cursor}) from exc
    created_at = payload.get("createdAt") if isinstance(payload, dict) else None
    row_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(created_at, str) or not isinstance(row_id, str):
        raise ValidationFailed("invalid ontology branch cursor", details={"cursor": cursor})
    return BranchPageCursor(created_at, row_id)


def branch_record(
    ctx: RequestContext, *, name: str, snapshot: ActiveOntologySnapshot, now: str
) -> OntologyBranchRecord:
    return OntologyBranchRecord(
        branch_id=_new_id("ontbranch"),
        tenant_id=ctx.tenant_id,
        name=name,
        status="open",
        base_version_id=snapshot.version["id"],
        base_version_number=snapshot.version["version_number"],
        base_yaml_text=snapshot.yaml_text,
        content_yaml_text=snapshot.yaml_text,
        content_fingerprint=yaml_fingerprint(snapshot.yaml_text),
        created_by=ctx.actor_user_id,
        created_at=now,
        updated_at=now,
    )


def branch_payload(row: OntologyBranchRow, *, is_base_stale: bool) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "baseVersionId": row["base_version_id"],
        "baseVersionNumber": row["base_version_number"],
        "baseStale": is_base_stale,
        "contentFingerprint": row["content_fingerprint"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "rebasedAt": row["rebased_at"],
        "proposalId": row["merged_proposal_id"],
        "mergedVersionNumber": row["merged_version_number"],
    }


def branch_detail_payload(row: OntologyBranchRow, *, is_base_stale: bool) -> dict[str, object]:
    payload = branch_payload(row, is_base_stale=is_base_stale)
    payload["yamlText"] = row["content_yaml_text"]
    payload["baseYamlText"] = row["base_yaml_text"]
    return payload


def branch_audit_ref(row: OntologyBranchRow) -> dict[str, object]:
    return {
        "name": row["name"],
        "status": row["status"],
        "baseVersionNumber": row["base_version_number"],
        "contentFingerprint": row["content_fingerprint"],
        "proposalId": row["merged_proposal_id"],
        "mergedVersionNumber": row["merged_version_number"],
    }


def require_branch_fingerprint_match(row: OntologyBranchRow, expected_fingerprint: str) -> None:
    required_text(expected_fingerprint, "expectedFingerprint")
    if row["content_fingerprint"] != expected_fingerprint:
        raise ConflictDetected(
            "ontology branch fingerprint mismatch",
            details={
                "branch_id": row["id"],
                "expectedFingerprint": expected_fingerprint,
                "contentFingerprint": row["content_fingerprint"],
            },
        )


def require_open_branch(row: OntologyBranchRow) -> None:
    if row["status"] != "open":
        raise ConflictDetected(
            "ontology branch is no longer open",
            details={"branch_id": row["id"], "status": row["status"]},
        )


def active_ontology_snapshot(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_repository: OntologyRepository,
) -> ActiveOntologySnapshot | None:
    """Render the ACTIVE ontology version to its YAML-equivalent text, if one exists."""
    version = ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
    if version is None:
        return None
    definition = _definition_for_version(conn, ctx, ontology_repository, version["id"])
    return ActiveOntologySnapshot(version=version, yaml_text=serialize_definition_yaml(definition))


def _definition_for_version(
    conn: TransactionContext,
    ctx: RequestContext,
    repository: OntologyRepository,
    ontology_version_id: str,
) -> JsonObject:
    object_rows = repository.object_types_for_version(
        transaction=conn, tenant_id=ctx.tenant_id, ontology_version_id=ontology_version_id
    )
    action_rows = [
        action
        for row in object_rows
        for action in repository.actions_for_target(transaction=conn, object_type_id=row["id"])
    ]
    return ontology_definition_snapshot(
        object_rows=object_rows,
        properties_by_object_id={
            row["id"]: repository.properties_for_object_type(transaction=conn, object_type_id=row["id"])
            for row in object_rows
        },
        link_rows=repository.link_types_for_version(
            transaction=conn, tenant_id=ctx.tenant_id, ontology_version_id=ontology_version_id
        ),
        action_rows=action_rows,
        interface_rows=repository.interface_types_for_version(
            transaction=conn, tenant_id=ctx.tenant_id, ontology_version_id=ontology_version_id
        ),
        function_rows=repository.function_types_for_version(
            transaction=conn, tenant_id=ctx.tenant_id, ontology_version_id=ontology_version_id
        ),
    )
