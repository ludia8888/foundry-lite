"""Pure command builders and invariant checks for external release delivery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from foundry_lite.application.ports.release_delivery_repository import (
    CompleteReleaseDeliveryCommand,
    PrepareReleaseDeliveryCommand,
    ReconcileReleaseDeliveryCommand,
    ReleaseDeliveryOperation,
    ReleaseDeliveryReconciledStatus,
    ReleaseDeliveryRecord,
    ReleaseDeliveryTerminalStatus,
)
from foundry_lite.application.ports.source_control_release import (
    PullRequestSearch,
    PullRequestSnapshot,
    SourceControlMergeReceipt,
    SourceRepositoryRef,
)
from foundry_lite.application.services.aip.external_release_delivery_lineage import ReleaseDeliveryLineage
from foundry_lite.application.services.aip.external_release_delivery_payloads import request_fingerprint
from foundry_lite.application.services.aip.external_release_rollback import (
    application_rollback_target,
    application_rollback_target_for_request,
    strict_rollback_reconciliation_candidates,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]
RECONCILIATION_SETTLE_SECONDS = 60


class ExternalReleaseDeliveryOutcomeUnknown(Exception):
    """A provider may have accepted a mutation, so blind replay is forbidden."""


def prepare_command(
    ctx: RequestContext,
    proposal_id: str,
    provider: str,
    operation: ReleaseDeliveryOperation,
    target_ref: dict[str, object],
    candidate_ref: dict[str, object],
    environment: str,
    idempotency_key: str,
    prior_resource_id: str | None,
    lineage: ReleaseDeliveryLineage,
) -> PrepareReleaseDeliveryCommand:
    ai_run_id, binding_hash = mutation_identity(ctx)
    now = utc_now()
    fingerprint = request_fingerprint(
        _delivery_identity(provider, operation, target_ref, candidate_ref, environment, prior_resource_id, lineage)
    )
    return PrepareReleaseDeliveryCommand(
        f"reldel_{uuid4().hex}",
        ctx.tenant_id,
        lineage.application_id,
        proposal_id,
        lineage.release_kind,
        lineage.workflow_run_id,
        lineage.parent_delivery_id,
        provider,
        operation,
        target_ref,
        candidate_ref,
        environment,
        idempotency_key,
        fingerprint,
        prior_resource_id,
        ai_run_id,
        binding_hash,
        ctx.request_id,
        ctx.actor_user_id,
        now,
        now,
    )


def _delivery_identity(
    provider: str,
    operation: ReleaseDeliveryOperation,
    target_ref: dict[str, object],
    candidate_ref: dict[str, object],
    environment: str,
    prior_resource_id: str | None,
    lineage: ReleaseDeliveryLineage,
) -> dict[str, object]:
    return {
        "provider": provider,
        "operation": operation,
        "target": target_ref,
        "candidate": candidate_ref,
        "environment": environment,
        "priorResourceId": prior_resource_id,
        "applicationId": lineage.application_id,
        "releaseKind": lineage.release_kind,
        "workflowRunId": lineage.workflow_run_id,
        "parentDeliveryId": lineage.parent_delivery_id,
    }


def complete_command(
    row: ReleaseDeliveryRecord,
    status: ReleaseDeliveryTerminalStatus,
    result_ref: dict[str, object] | None,
    error_ref: dict[str, object] | None,
    provider_resource_id: str | None,
) -> CompleteReleaseDeliveryCommand:
    now = utc_now()
    return CompleteReleaseDeliveryCommand(
        row.tenant_id,
        row.delivery_id,
        "dispatching",
        row.execution_attempt,
        status,
        provider_resource_id,
        provider_resource_id,
        row.prior_resource_id,
        result_ref,
        error_ref,
        now,
        now,
    )


def reconcile_command(
    row: ReleaseDeliveryRecord,
    status: str,
    result_ref: dict[str, object] | None,
    error_ref: dict[str, object] | None,
    provider_resource_id: str | None,
) -> ReconcileReleaseDeliveryCommand:
    if status not in {"landed", "absent", "failed"}:
        raise ValueError("ambiguous delivery may reconcile only to landed, absent, or failed")
    now = utc_now()
    return ReconcileReleaseDeliveryCommand(
        row.tenant_id,
        row.delivery_id,
        "ambiguous",
        row.execution_attempt,
        cast(ReleaseDeliveryReconciledStatus, status),
        provider_resource_id,
        provider_resource_id,
        row.prior_resource_id,
        result_ref,
        error_ref,
        now,
        now,
    )


def require_snapshot_binding(snapshot: PullRequestSnapshot, arguments: JsonObject) -> None:
    expected = {
        "expectedSourceBaseSha": snapshot.base_sha,
        "expectedSourceHeadSha": snapshot.target.expected_head_sha,
        "expectedSourceChecksFingerprint": snapshot.checks_fingerprint,
        "expectedSourceRulesFingerprint": snapshot.rules_fingerprint,
    }
    for key, value in expected.items():
        if required_text(arguments, key) != value:
            raise ConflictDetected(
                "source release evidence changed after review",
                details={"field": key, "expected": value},
            )


def require_source_replay_binding(
    row: ReleaseDeliveryRecord,
    ctx: RequestContext,
    proposal_id: str,
    repository: SourceRepositoryRef,
    expected_base_ref: str,
    expected_head_ref: str,
    arguments: JsonObject,
) -> None:
    """Bind an idempotent replay to its stored reviewed source candidate."""

    expected_target = {
        "repositoryId": repository.repository_id,
        "repositoryOwner": repository.owner,
        "repositoryName": repository.name,
        "baseRef": expected_base_ref,
    }
    ai_run_id, binding_hash = mutation_identity(ctx)
    if (
        row.proposal_id != proposal_id
        or row.provider != repository.provider
        or row.environment != expected_base_ref
        or row.ai_run_id != ai_run_id
        or row.binding_hash != binding_hash
        or row.created_by != ctx.actor_user_id
    ):
        raise ConflictDetected("source release replay does not match the governed proposal")
    for key, value in expected_target.items():
        _require_stored_value(row.target_ref, key, value)
    _require_stored_value(row.candidate_ref, "headRef", expected_head_ref)
    _require_stored_value(row.target_ref, "headSha", required_candidate_text(row, "headSha"))
    _require_replay_arguments(row, arguments)


def _require_replay_arguments(row: ReleaseDeliveryRecord, arguments: JsonObject) -> None:
    expected = {
        "expectedSourceBaseSha": required_candidate_text(row, "baseSha"),
        "expectedSourceHeadSha": required_candidate_text(row, "headSha"),
        "expectedSourceChecksFingerprint": required_candidate_text(row, "checksFingerprint"),
        "expectedSourceRulesFingerprint": required_candidate_text(row, "rulesFingerprint"),
    }
    for key, value in expected.items():
        if required_text(arguments, key) != value:
            raise ConflictDetected(
                "source release evidence changed after review",
                details={"field": key, "expected": value},
            )


def _require_stored_value(payload: JsonObject | None, key: str, expected: object) -> None:
    if payload is None or payload.get(key) != expected:
        raise ConflictDetected(
            "stored source release target no longer matches the server binding",
            details={"field": key, "expected": expected},
        )


def require_server_source_target(snapshot: PullRequestSnapshot, search: PullRequestSearch) -> None:
    target = snapshot.target
    if (
        target.repository != search.repository
        or target.expected_base_ref != search.expected_base_ref
        or snapshot.head_ref != search.expected_head_ref
    ):
        raise ConflictDetected("source-control adapter returned a target outside the server binding")


def receipt_matches_delivery(receipt: SourceControlMergeReceipt, row: ReleaseDeliveryRecord) -> bool:
    return (
        receipt.repository_id == row.target_ref.get("repositoryId")
        and receipt.pull_number == row.target_ref.get("pullNumber")
        and receipt.head_sha == row.target_ref.get("headSha")
    )


def proposal_id(proposal: JsonObject) -> str:
    return required_text(proposal, "id")


def proposal_branch_name(proposal: JsonObject) -> str:
    source = proposal.get("sourceBranch")
    if not isinstance(source, Mapping):
        raise ValidationFailed("release proposal has no server-resolved source branch")
    return required_text(source, "branchName")


def required_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required")
    return value.strip()


def required_candidate_text(row: ReleaseDeliveryRecord, key: str) -> str:
    if row.candidate_ref is None:
        raise ValidationFailed("stored release delivery candidate is missing")
    return required_text(row.candidate_ref, key)


def delivery_commit_id(row: ReleaseDeliveryRecord) -> str:
    key = "commitId" if row.operation == "application_deploy" else "targetCommitId"
    return required_candidate_text(row, key)


def mutation_identity(ctx: RequestContext) -> tuple[str, str]:
    run_id = ctx.governed_release_run_id
    binding_hash = ctx.governed_release_binding_hash
    if not isinstance(run_id, str) or not isinstance(binding_hash, str):
        raise ValidationFailed("external release delivery requires governed action provenance")
    return run_id, binding_hash


def trace_identity(ctx: RequestContext) -> str:
    run_id, _ = mutation_identity(ctx)
    return run_id


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def is_settled_window(row: ReleaseDeliveryRecord) -> bool:
    """Wait from the durable provider-dispatch timestamp before proving absence."""

    if not row.dispatch_started_at:
        return False
    try:
        started_at = datetime.fromisoformat(row.dispatch_started_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        return False
    elapsed = datetime.now(UTC) - started_at.astimezone(UTC)
    return elapsed >= timedelta(seconds=RECONCILIATION_SETTLE_SECONDS)


def delivery_dispatch_started_at(row: ReleaseDeliveryRecord) -> datetime | None:
    """Return the durable provider-dispatch timestamp when it is valid."""

    if not row.dispatch_started_at:
        return None
    try:
        parsed = datetime.fromisoformat(row.dispatch_started_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "ExternalReleaseDeliveryOutcomeUnknown",
    "application_rollback_target",
    "application_rollback_target_for_request",
    "complete_command",
    "delivery_commit_id",
    "delivery_dispatch_started_at",
    "is_settled_window",
    "parse_timestamp",
    "prepare_command",
    "proposal_branch_name",
    "proposal_id",
    "receipt_matches_delivery",
    "reconcile_command",
    "require_server_source_target",
    "require_source_replay_binding",
    "require_snapshot_binding",
    "required_candidate_text",
    "required_text",
    "strict_rollback_reconciliation_candidates",
    "trace_identity",
    "utc_now",
]
