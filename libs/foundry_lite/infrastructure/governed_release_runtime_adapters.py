"""External release provider adapters exposed behind one composition boundary."""

from foundry_lite.infrastructure.adapters.github_release import GitHubReleaseAdapter, GitHubReleaseConfig
from foundry_lite.infrastructure.adapters.kubernetes_deployment import (
    KubernetesDeploymentConfig,
    KubernetesInfrastructureDeploymentAdapter,
)
from foundry_lite.infrastructure.adapters.render_deployment import RenderInfrastructureDeploymentAdapter

__all__ = [
    "GitHubReleaseAdapter",
    "GitHubReleaseConfig",
    "KubernetesDeploymentConfig",
    "KubernetesInfrastructureDeploymentAdapter",
    "RenderInfrastructureDeploymentAdapter",
]
