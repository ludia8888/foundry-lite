"""Governed Release dependency bundle kept behind one architecture edge."""

from __future__ import annotations

from dataclasses import dataclass, field

from foundry_lite.application.dependency_compat import required_dependency
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationRepository,
    GovernedReleaseLiveAuthority,
    GovernedReleaseMcpAuthority,
    UnavailableGovernedReleaseLiveAttestationRepository,
)
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    UnavailableInfrastructureDeploymentAdapter,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
    ReleaseDeliveryRepository,
    ReleaseDeliveryTerminalStatus,
)
from foundry_lite.application.ports.source_control_release import (
    PullRequestSearch,
    PullRequestSnapshot,
    PullRequestTarget,
    SourceControlReleasePort,
    SourceRepositoryRef,
    UnavailableSourceControlReleasePort,
)


@dataclass(frozen=True)
class GovernedReleaseDependencies:
    """Provider adapters, durable ledger, and server-owned target binding."""

    config: GovernedReleaseDeliveryConfig = field(default_factory=GovernedReleaseDeliveryConfig)
    source_control_adapter: SourceControlReleasePort = field(default_factory=UnavailableSourceControlReleasePort)
    infrastructure_deployment_adapter: InfrastructureDeploymentAdapter = field(
        default_factory=UnavailableInfrastructureDeploymentAdapter
    )
    delivery_repository: ReleaseDeliveryRepository | None = None
    live_attestation_repository: GovernedReleaseLiveAttestationRepository = field(
        default_factory=UnavailableGovernedReleaseLiveAttestationRepository
    )
    live_authority: GovernedReleaseLiveAuthority = field(
        default_factory=lambda: GovernedReleaseLiveAuthority(
            runtime_profile="local",
            database_backend="unavailable",
            source_provider_profile="source-control-unavailable",
            deployment_provider_profile="unavailable-infrastructure-deployment",
            source_revision="",
        )
    )


class GovernedReleaseDependencyAccessors:
    """Typed release accessors shared by the size-limited dependency facade."""

    def _governed_release_dependencies(self) -> GovernedReleaseDependencies:
        raise NotImplementedError

    @property
    def governed_release_delivery_config(self) -> GovernedReleaseDeliveryConfig:
        return self._governed_release_dependencies().config

    @property
    def source_control_release_adapter(self) -> SourceControlReleasePort:
        return self._governed_release_dependencies().source_control_adapter

    @property
    def infrastructure_deployment_adapter(self) -> InfrastructureDeploymentAdapter:
        return self._governed_release_dependencies().infrastructure_deployment_adapter

    @property
    def release_delivery_repository(self) -> ReleaseDeliveryRepository:
        return required_dependency(
            self._governed_release_dependencies().delivery_repository,
            "governed release delivery repository unavailable",
        )

    @property
    def governed_release_live_attestation_repository(self) -> GovernedReleaseLiveAttestationRepository:
        return self._governed_release_dependencies().live_attestation_repository

    @property
    def governed_release_live_authority(self) -> GovernedReleaseLiveAuthority:
        return self._governed_release_dependencies().live_authority


__all__ = [
    "GovernedReleaseDeliveryConfig",
    "GovernedReleaseDependencyAccessors",
    "GovernedReleaseDependencies",
    "GovernedReleaseLiveAttestationRepository",
    "GovernedReleaseLiveAuthority",
    "GovernedReleaseMcpAuthority",
    "InfrastructureDeploymentAdapter",
    "PullRequestSearch",
    "PullRequestSnapshot",
    "PullRequestTarget",
    "ReleaseDeliveryOperation",
    "ReleaseDeliveryRecord",
    "ReleaseDeliveryRepository",
    "ReleaseDeliveryTerminalStatus",
    "SourceControlReleasePort",
    "SourceRepositoryRef",
]
