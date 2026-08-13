"""Fail-closed live deployment-policy preflight before a source merge."""

from __future__ import annotations

from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import InfrastructureDeploymentAdapter
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.services.aip.external_release_infrastructure_evidence import (
    ManualDeploymentPolicyFailure,
    require_manual_deployment_policy_for_service,
)
from foundry_lite.domain.context import RequestContext


def require_manual_deployment_policy(
    ctx: RequestContext,
    row: ReleaseDeliveryRecord,
    config: GovernedReleaseDeliveryConfig,
    adapter: InfrastructureDeploymentAdapter,
) -> None:
    service_id = config.deployment_service_id
    if not service_id:
        return
    repository = config.source_repository
    if repository is None:
        raise ManualDeploymentPolicyFailure(
            {
                "kind": "deployment_policy_preflight_failed",
                "reason": "source_repository_binding_is_missing",
                "knownNotCommitted": True,
                "safeToRetry": True,
            }
        )
    require_manual_deployment_policy_for_service(
        ctx,
        row,
        service_id,
        adapter,
        repository.owner,
        repository.name,
        config.source_base_ref,
    )


__all__ = [
    "ManualDeploymentPolicyFailure",
    "require_manual_deployment_policy",
    "require_manual_deployment_policy_for_service",
]
