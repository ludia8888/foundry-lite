"""Project current append-only live attestation readiness."""

from __future__ import annotations

from datetime import datetime

from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationRecord,
    GovernedReleaseLiveAuthority,
)
from foundry_lite.application.services.aip.governed_release_live_attestation_writer import (
    LIVE_ATTESTATION_SCHEMA,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import READINESS_SCHEMA


def live_readiness_blocker(
    application_id: str,
    authority: GovernedReleaseLiveAuthority,
    row: GovernedReleaseLiveAttestationRecord | None,
    configuration_fingerprint: str,
    now: datetime,
) -> str | None:
    if not authority.is_live_eligible_for(application_id):
        return "authentic_live_collector_not_eligible"
    if row is None:
        return "authentic_live_attestation_missing"
    if row.schema_version != LIVE_ATTESTATION_SCHEMA:
        return "authentic_live_attestation_schema_mismatch"
    if row.configuration_fingerprint != configuration_fingerprint:
        return "authentic_live_attestation_configuration_stale"
    if _is_expired(row.valid_until, now):
        return "authentic_live_attestation_expired"
    return None


def live_readiness_projection(
    application_id: str,
    authority: GovernedReleaseLiveAuthority,
    row: GovernedReleaseLiveAttestationRecord | None,
    blocker: str | None,
    configuration_fingerprint: str,
) -> dict[str, object]:
    is_ready = authority.is_live_eligible_for(application_id)
    verified_row = _verified_row(is_ready, blocker, row)
    is_live = verified_row is not None
    return {
        "schema_version": READINESS_SCHEMA,
        "attestation_purpose": "rollback_rehearsal",
        "application_id": application_id,
        "status": _readiness_status(is_ready, is_live),
        "is_ready_for_live_run": is_ready,
        "is_live_verified": is_live,
        "checks": _readiness_checks(is_ready, is_live, blocker),
        "blockers": _readiness_blockers(is_live, blocker),
        "configuration_fingerprint": configuration_fingerprint,
        **_live_evidence(verified_row),
    }


def _verified_row(
    is_ready: bool,
    blocker: str | None,
    row: GovernedReleaseLiveAttestationRecord | None,
) -> GovernedReleaseLiveAttestationRecord | None:
    if not is_ready or blocker is not None or row is None:
        return None
    return row


def _readiness_status(is_ready: bool, is_live: bool) -> str:
    if is_live:
        return "live_verified"
    if is_ready:
        return "ready_for_live_run"
    return "blocked"


def _readiness_checks(is_ready: bool, is_live: bool, blocker: str | None) -> list[dict[str, str]]:
    collector_blocker = None if is_ready else blocker
    attestation_blocker = None if is_live else blocker or "authentic_live_attestation_missing"
    return [
        _check("collector_authority", collector_blocker),
        _check("authentic_live_attestation", attestation_blocker),
    ]


def _readiness_blockers(is_live: bool, blocker: str | None) -> list[str]:
    if is_live:
        return []
    return [blocker or "authentic_live_attestation_missing"]


def _live_evidence(row: GovernedReleaseLiveAttestationRecord | None) -> dict[str, object]:
    if row is None:
        return {"manifest_digest": None, "evidence_digest": None, "attestation": None}
    return {
        "manifest_digest": row.manifest_digest,
        "evidence_digest": row.evidence_digest,
        "attestation": _attestation_summary(row),
    }


def _check(name: str, blocker: str | None) -> dict[str, str]:
    return {
        "name": name,
        "status": "passed" if blocker is None else "blocked",
        "code": "verified" if blocker is None else blocker,
    }


def _attestation_summary(row: GovernedReleaseLiveAttestationRecord) -> dict[str, object]:
    return {
        "attestationPurpose": "rollback_rehearsal",
        "attestationId": row.attestation_id,
        "collectorRunId": row.collector_run_id,
        "collectorVersion": row.collector_version,
        "sourceRevision": row.source_revision,
        "collectedAt": row.collected_at,
        "validUntil": row.valid_until,
        "ontologyWorkflowRunId": row.ontology_workflow_run_id,
        "pipelineWorkflowRunId": row.pipeline_workflow_run_id,
    }


def _is_expired(value: str, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed.tzinfo is None or parsed <= now


__all__ = ["live_readiness_blocker", "live_readiness_projection"]
