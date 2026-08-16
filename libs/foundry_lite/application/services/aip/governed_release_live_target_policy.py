"""Provider-neutral target-policy evidence for the hosted live collector."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentServicePolicyObservation,
)
from foundry_lite.application.ports.source_control_candidate import SourceRefSnapshot


def target_configuration_evidence(
    config: GovernedReleaseDeliveryConfig,
    source: SourceRefSnapshot,
    policy: InfrastructureDeploymentServicePolicyObservation,
) -> dict[str, object]:
    """Return stable, public-safe source and deployment target coordinates."""

    repository = config.source_repository
    binding = policy.source_binding
    return {
        "sourceProvider": repository.provider if repository is not None else None,
        "repositoryId": repository.repository_id if repository is not None else None,
        "repositoryOwner": repository.owner if repository is not None else None,
        "repositoryName": repository.name if repository is not None else None,
        "baseRef": config.source_base_ref,
        "baseCommitSha": source.commit_sha,
        "baseTreeSha": source.tree_sha,
        "provider": policy.provider,
        "serviceId": policy.service_id,
        "releaseMode": policy.release_mode,
        "triggerMode": policy.trigger_mode,
        "deploymentSourceProvider": binding.provider if binding else None,
        "sourceRepositoryOwner": binding.repository_owner if binding else None,
        "sourceRepositoryName": binding.repository_name if binding else None,
        "sourceRef": binding.ref if binding else None,
        "workloadKind": policy.workload_kind,
        "isSuspended": policy.is_suspended,
        "providerRequestId": policy.provider_request_id,
    }


def target_configuration_matches(
    config: GovernedReleaseDeliveryConfig,
    source: SourceRefSnapshot,
    policy: InfrastructureDeploymentServicePolicyObservation,
    expected_deployment_provider: str,
) -> bool:
    """Compare provider observations with the server-owned target policy."""

    repository = config.source_repository
    if repository is None:
        return False
    binding = policy.source_binding
    source_matches = (source.repository, source.ref) == (repository, config.source_base_ref)
    actual = (
        policy.provider,
        policy.service_id,
        policy.release_mode,
        policy.trigger_mode,
        binding.provider if binding else None,
        binding.repository_owner.casefold() if binding else None,
        binding.repository_name.casefold() if binding else None,
        binding.ref if binding else None,
        policy.workload_kind,
        policy.is_suspended,
    )
    expected = (
        expected_deployment_provider,
        config.deployment_service_id,
        config.deployment_release_mode,
        "manual",
        repository.provider,
        repository.owner.casefold(),
        repository.name.casefold(),
        config.source_base_ref,
        config.deployment_workload_kind,
        False,
    )
    return source_matches and actual == expected


def stored_target_configuration_matches(
    config: GovernedReleaseDeliveryConfig,
    evidence: Mapping[str, object],
    deployment_provider: str,
    provider_request_id: str | None,
) -> bool:
    """Validate persisted collector evidence against server-owned coordinates."""

    repository = config.source_repository
    if repository is None:
        return False
    expected = {
        "sourceProvider": repository.provider,
        "repositoryId": repository.repository_id,
        "repositoryOwner": repository.owner,
        "repositoryName": repository.name,
        "baseRef": config.source_base_ref,
        "provider": deployment_provider,
        "serviceId": config.deployment_service_id,
        "releaseMode": config.deployment_release_mode,
        "triggerMode": "manual",
        "deploymentSourceProvider": repository.provider,
        "sourceRepositoryOwner": repository.owner,
        "sourceRepositoryName": repository.name,
        "sourceRef": config.source_base_ref,
        "workloadKind": config.deployment_workload_kind,
        "isSuspended": False,
        "providerRequestId": provider_request_id,
    }
    return all(evidence.get(key) == value for key, value in expected.items())


__all__ = [
    "stored_target_configuration_matches",
    "target_configuration_evidence",
    "target_configuration_matches",
]
