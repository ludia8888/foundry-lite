"""Port for provider-backed application infrastructure deployments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)

InfrastructureDeploymentOperation = Literal[
    "get_service_policy",
    "start",
    "get",
    "list_candidates",
    "rollback",
]
InfrastructureDeploymentStatus = Literal[
    "queued",
    "building",
    "preparing",
    "deploying",
    "live",
    "deactivated",
    "failed",
    "canceled",
    "unknown",
]
InfrastructureDeploymentMutationOutcome = Literal["accepted", "outcome_unknown"]


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentStartRequest:
    """Start an exact reviewed revision on one provider service."""

    tenant_id: str
    service_id: str
    commit_id: str
    idempotency_key: str
    request_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentServicePolicyRequest:
    """Read the live deployment policy for one provider service."""

    tenant_id: str
    service_id: str
    request_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentServicePolicyObservation:
    """Normalized live policy evidence used before an external source merge."""

    provider: str
    service_id: str
    is_auto_deploy_enabled: bool
    source_repository_owner: str
    source_repository_name: str
    source_branch: str
    service_type: str
    is_suspended: bool
    provider_request_id: str | None


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentGetRequest:
    """Read one provider deployment without changing remote state."""

    tenant_id: str
    service_id: str
    deploy_id: str
    request_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentCandidateQuery:
    """Bound a reconciliation search to one exact revision and time window."""

    tenant_id: str
    service_id: str
    commit_id: str
    created_after: datetime
    created_before: datetime
    request_id: str
    correlation_id: str
    limit: int = 20


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentRollbackRequest:
    """Start a rollback to a previously verified provider deployment."""

    tenant_id: str
    service_id: str
    target_deploy_id: str
    target_commit_id: str
    idempotency_key: str
    request_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentObservation:
    """Normalized provider deployment state safe for durable operator evidence."""

    provider: str
    service_id: str
    deploy_id: str
    status: InfrastructureDeploymentStatus
    provider_status: str
    commit_id: str | None
    trigger: str | None
    created_at: datetime | None
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None
    is_terminal: bool
    is_successful: bool
    provider_request_id: str | None


@dataclass(frozen=True, slots=True)
class InfrastructureDeploymentMutationResult:
    """A provider receipt, or an explicit instruction to reconcile without retry."""

    operation: Literal["start", "rollback"]
    outcome: InfrastructureDeploymentMutationOutcome
    provider_http_status: int
    observation: InfrastructureDeploymentObservation | None
    rollback_target_deploy_id: str | None
    is_safe_to_retry: bool
    reason: str


class InfrastructureDeploymentOutcomeUnknown(AdapterError):
    """The provider might have accepted a mutation, so blind retry is forbidden."""

    code = "INFRASTRUCTURE_DEPLOYMENT_OUTCOME_UNKNOWN"
    is_safe_to_retry = False
    is_known_not_committed = False


class InfrastructureDeploymentAdapter(Protocol):
    """Deploy and observe application infrastructure behind a provider boundary."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract: ...

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation: ...

    def start(
        self,
        request: InfrastructureDeploymentStartRequest,
    ) -> InfrastructureDeploymentMutationResult: ...

    def get(
        self,
        request: InfrastructureDeploymentGetRequest,
    ) -> InfrastructureDeploymentObservation: ...

    def list_candidates(
        self,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[InfrastructureDeploymentObservation, ...]: ...

    def rollback(
        self,
        request: InfrastructureDeploymentRollbackRequest,
    ) -> InfrastructureDeploymentMutationResult: ...


class UnavailableInfrastructureDeploymentAdapter:
    """Fail closed until an infrastructure deployment provider is configured."""

    profile_name = "unavailable-infrastructure-deployment"

    def failure_contract(self) -> AdapterFailureContract:
        modes = tuple(
            AdapterFailureMode(operation, "unavailable", False, "Infrastructure deployment is unavailable.")
            for operation in ("get_service_policy", "start", "get", "list_candidates", "rollback")
        )
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=modes)

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        raise self._error("get_service_policy")

    def start(
        self,
        request: InfrastructureDeploymentStartRequest,
    ) -> InfrastructureDeploymentMutationResult:
        raise self._error("start", request.idempotency_key)

    def get(
        self,
        request: InfrastructureDeploymentGetRequest,
    ) -> InfrastructureDeploymentObservation:
        raise self._error("get")

    def list_candidates(
        self,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[InfrastructureDeploymentObservation, ...]:
        raise self._error("list_candidates")

    def rollback(
        self,
        request: InfrastructureDeploymentRollbackRequest,
    ) -> InfrastructureDeploymentMutationResult:
        raise self._error("rollback", request.idempotency_key)

    def _error(self, operation: InfrastructureDeploymentOperation, idempotency_key: str | None = None) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation=operation,
                kind="unavailable",
                is_retryable=False,
                operator_message="Infrastructure deployment provider is not configured.",
                idempotency_key=idempotency_key,
                details={"reason": "provider_not_configured"},
            )
        )


__all__ = [
    "InfrastructureDeploymentAdapter",
    "InfrastructureDeploymentCandidateQuery",
    "InfrastructureDeploymentGetRequest",
    "InfrastructureDeploymentMutationResult",
    "InfrastructureDeploymentObservation",
    "InfrastructureDeploymentOperation",
    "InfrastructureDeploymentOutcomeUnknown",
    "InfrastructureDeploymentRollbackRequest",
    "InfrastructureDeploymentServicePolicyObservation",
    "InfrastructureDeploymentServicePolicyRequest",
    "InfrastructureDeploymentStartRequest",
    "InfrastructureDeploymentStatus",
    "UnavailableInfrastructureDeploymentAdapter",
]
