"""Pure identity and dispatch-window checks for infrastructure receipts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentServicePolicyRequest,
)
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.domain.context import RequestContext


class ManualDeploymentPolicyFailure(Exception):
    """The live provider policy did not prove a manual-only boundary."""

    def __init__(self, error_ref: dict[str, object]) -> None:
        super().__init__("manual deployment policy was not verified")
        self._error_ref = error_ref

    @property
    def error_ref(self) -> dict[str, object]:
        return self._error_ref


def require_manual_deployment_policy_for_service(
    ctx: RequestContext,
    row: ReleaseDeliveryRecord,
    service_id: str,
    adapter: InfrastructureDeploymentAdapter,
    expected_repository_owner: str,
    expected_repository_name: str,
    expected_branch: str,
) -> None:
    """Require one exact service to remain manual immediately before mutation."""

    request = InfrastructureDeploymentServicePolicyRequest(
        ctx.tenant_id,
        service_id,
        ctx.request_id,
        row.ai_run_id,
    )
    try:
        policy = adapter.get_service_policy(request)
    except Exception as exc:
        raise ManualDeploymentPolicyFailure(_policy_failure_evidence(exc)) from exc
    expected_provider = adapter.profile_name.split("-", 1)[0]
    if not _service_policy_matches(
        policy,
        expected_provider=expected_provider,
        expected_service_id=service_id,
        expected_repository_owner=expected_repository_owner,
        expected_repository_name=expected_repository_name,
        expected_branch=expected_branch,
    ):
        raise ManualDeploymentPolicyFailure(
            {
                "kind": "deployment_policy_preflight_failed",
                "reason": "manual_deploy_or_source_binding_not_verified",
                "knownNotCommitted": True,
                "safeToRetry": True,
            }
        )


def _service_policy_matches(
    policy: object,
    *,
    expected_provider: str,
    expected_service_id: str,
    expected_repository_owner: str,
    expected_repository_name: str,
    expected_branch: str,
) -> bool:
    return bool(
        getattr(policy, "provider", None) == expected_provider
        and getattr(policy, "service_id", None) == expected_service_id
        and getattr(policy, "is_auto_deploy_enabled", None) is False
        and _same_coordinate(getattr(policy, "source_repository_owner", None), expected_repository_owner)
        and _same_coordinate(getattr(policy, "source_repository_name", None), expected_repository_name)
        and getattr(policy, "source_branch", None) == expected_branch
        and getattr(policy, "service_type", None) == "web_service"
        and getattr(policy, "is_suspended", None) is False
    )


def _same_coordinate(actual: object, expected: str) -> bool:
    return isinstance(actual, str) and actual.casefold() == expected.casefold()


def _policy_failure_evidence(exc: Exception) -> dict[str, object]:
    evidence: dict[str, object]
    if isinstance(exc, AdapterError):
        evidence = dict(adapter_failure_payload(exc))
    else:
        evidence = {"type": type(exc).__name__}
    evidence.update(
        {
            "kind": "deployment_policy_preflight_failed",
            "knownNotCommitted": True,
            "safeToRetry": True,
        }
    )
    return evidence


def infrastructure_receipt_matches_delivery(
    row: ReleaseDeliveryRecord,
    observation: InfrastructureDeploymentObservation,
) -> bool:
    """Bind an accepted provider receipt to the exact durable delivery intent."""

    service_id = _mapping_text(row.target_ref, "serviceId")
    expected_commit = _delivery_commit_id(row)
    if not service_id or not expected_commit:
        return False
    return (
        observation.provider == row.provider
        and observation.service_id == service_id
        and observation.commit_id == expected_commit
        and _trigger_matches_operation(row, observation)
        and _is_new_provider_resource(row, observation.deploy_id)
    )


def strict_deploy_reconciliation_candidates(
    row: ReleaseDeliveryRecord,
    candidates: Sequence[InfrastructureDeploymentObservation],
    *,
    observed_at: datetime,
) -> tuple[InfrastructureDeploymentObservation, ...]:
    """Keep only exact deploy receipts created inside this dispatch window."""

    dispatch_started_at = _aware_timestamp(row.dispatch_started_at)
    if row.operation != "application_deploy" or not _is_valid_window(dispatch_started_at, observed_at):
        return ()
    return tuple(
        candidate
        for candidate in candidates
        if infrastructure_receipt_matches_delivery(row, candidate)
        and candidate.created_at is not None
        and dispatch_started_at is not None
        and dispatch_started_at <= candidate.created_at <= observed_at
    )


def delivery_dispatch_started_at(row: ReleaseDeliveryRecord) -> datetime | None:
    """Return the durable, timezone-aware start of the provider mutation."""

    return _aware_timestamp(row.dispatch_started_at)


def _trigger_matches_operation(
    row: ReleaseDeliveryRecord,
    observation: InfrastructureDeploymentObservation,
) -> bool:
    if row.operation == "application_rollback":
        return observation.trigger == "rollback"
    if row.operation == "application_deploy":
        return observation.trigger == "api"
    return False


def _is_new_provider_resource(row: ReleaseDeliveryRecord, deploy_id: str) -> bool:
    excluded = {value for value in (row.prior_resource_id, _candidate_text(row, "targetDeployId")) if value}
    return deploy_id not in excluded


def _delivery_commit_id(row: ReleaseDeliveryRecord) -> str | None:
    key = "commitId" if row.operation == "application_deploy" else "targetCommitId"
    return _candidate_text(row, key)


def _candidate_text(row: ReleaseDeliveryRecord, key: str) -> str | None:
    return _mapping_text(row.candidate_ref, key)


def _mapping_text(payload: Mapping[str, object] | None, key: str) -> str | None:
    value = payload.get(key) if payload is not None else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _aware_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _is_valid_window(started_at: datetime | None, observed_at: datetime) -> bool:
    return (
        started_at is not None
        and observed_at.tzinfo is not None
        and observed_at.utcoffset() is not None
        and started_at <= observed_at
    )


__all__ = [
    "ManualDeploymentPolicyFailure",
    "delivery_dispatch_started_at",
    "infrastructure_receipt_matches_delivery",
    "require_manual_deployment_policy_for_service",
    "strict_deploy_reconciliation_candidates",
]
