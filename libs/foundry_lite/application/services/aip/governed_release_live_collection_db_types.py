"""Typed values shared by bounded Governed Release DB collection modules."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn, cast

from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    ReleaseKind,
    ReleaseTool,
    ServerActionClaim,
    ServerDeliveryClaim,
)
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ServerActionResultClaim:
    """Canonical result proof for one selected successful tool ledger."""

    release_kind: ReleaseKind
    tool_name: ReleaseTool
    ai_run_id: str
    result_fingerprint: str
    result_json: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ServerActionAuditClaim:
    """One exact durable succeeded audit event selected for an action."""

    release_kind: ReleaseKind
    tool_name: ReleaseTool
    ai_run_id: str
    event_id: str


@dataclass(frozen=True, slots=True)
class ServerLoadedDatabaseSnapshot:
    """Frozen DB selection used before and after concrete provider readback."""

    tenant_id: str
    application_id: str
    ontology_workflow_run_id: str
    pipeline_workflow_run_id: str
    ontology_proposal_id: str
    pipeline_proposal_id: str
    authorization_policy_fingerprint: str
    submitter_subject_hash: str
    submitter_oauth_session_hash: str
    reviewer_subject_hash: str
    reviewer_oauth_session_hash: str
    is_authorization_code_human_grant: bool
    authorization_policy: Mapping[str, object]
    actions: tuple[ServerActionClaim, ...]
    action_results: tuple[ServerActionResultClaim, ...]
    action_audits: tuple[ServerActionAuditClaim, ...]
    deliveries: tuple[ServerDeliveryClaim, ...]
    delivery_records: tuple[ReleaseDeliveryRecord, ...]
    selected_audit_event_ids: tuple[str, ...]
    initial_database_fingerprint: str
    initial_read_at: datetime


@dataclass(frozen=True, slots=True)
class SelectedAuditEvidence:
    event_id: str
    actor_user_id: str
    ai_run_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ActionLedgerSource:
    release_kind: ReleaseKind
    proposal_id: str
    tool_name: ReleaseTool
    ai_run_id: str
    audit: SelectedAuditEvidence | None


@dataclass(frozen=True, slots=True)
class LoadedActionLedger:
    claim: ServerActionClaim
    result: ServerActionResultClaim
    actor_user_id: str
    request_id: str
    policy_fingerprint: str
    authorization_policy: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LoadedAuthBinding:
    actor_user_id: str
    session_id: str
    oauth_session_hash: str
    idempotency_key: str
    arguments_hash: str
    policy_fingerprint: str
    authorization_policy: Mapping[str, object]


def row_time(row: Mapping[str, object], key: str) -> datetime:
    return parse_time(row.get(key), f"{key}_invalid")


def parse_time(value: object, reason: str) -> datetime:
    if not isinstance(value, str):
        invalid(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        invalid(reason)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        invalid(reason)
    return parsed


def required_row_text(row: Mapping[str, object], key: str, reason: str) -> str:
    value = row.get(key)
    if not is_text(value):
        invalid(reason)
    return cast(str, value)


def is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def invalid(reason: str) -> NoReturn:
    raise ValidationFailed("Governed release live collection DB evidence is invalid", details={"reason": reason})


def conflict(reason: str) -> NoReturn:
    raise ConflictDetected("Governed release live collection DB evidence is ambiguous", details={"reason": reason})


__all__ = [
    "ServerActionAuditClaim",
    "ServerActionResultClaim",
    "ServerLoadedDatabaseSnapshot",
]
