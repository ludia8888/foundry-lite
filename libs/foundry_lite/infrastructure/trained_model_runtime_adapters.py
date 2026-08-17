"""Code and trained-model execution adapters composed behind one boundary."""

from __future__ import annotations

import os

from foundry_lite.application.ports.code_execution import CodeExecutionAdapter
from foundry_lite.application.ports.trained_model_inference import TrainedModelInferencePort
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.infrastructure.adapters.container_code_execution import ContainerCodeExecutionAdapter
from foundry_lite.infrastructure.adapters.container_trained_model_inference import (
    ContainerTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.kubernetes_job_code_execution import KubernetesJobCodeExecutionAdapter
from foundry_lite.infrastructure.adapters.kubernetes_job_trained_model_inference import (
    KubernetesJobTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.local_trained_model_inference import (
    LocalTrainedModelInferenceAdapter,
)


def code_execution_adapter(
    compute_profile: str,
    runtime_profile: RuntimeProfile,
) -> CodeExecutionAdapter:
    """Select the isolated code executor for the requested runtime profile."""

    if compute_profile == "kubernetes-job":
        return KubernetesJobCodeExecutionAdapter()
    return ContainerCodeExecutionAdapter(is_image_digest_required=runtime_profile.is_protected)


def trained_model_inference_adapter(runtime_profile: RuntimeProfile) -> TrainedModelInferencePort:
    """Select inference execution while forbidding local work in protected profiles."""

    default_profile = "local" if runtime_profile.is_local_like else "container"
    profile = os.getenv("FOUNDRY_LITE_TRAINED_MODEL_PROFILE", default_profile).strip().lower().replace("_", "-")
    if profile == "local" and runtime_profile.is_local_like:
        return LocalTrainedModelInferenceAdapter()
    if profile == "container":
        return ContainerTrainedModelInferenceAdapter(
            is_image_digest_required=runtime_profile.is_protected,
        )
    if profile == "kubernetes-job":
        return KubernetesJobTrainedModelInferenceAdapter()
    raise ValueError("protected runtimes require a container or kubernetes-job trained-model profile")


__all__ = [
    "CodeExecutionAdapter",
    "code_execution_adapter",
    "ContainerTrainedModelInferenceAdapter",
    "LocalTrainedModelInferenceAdapter",
    "KubernetesJobTrainedModelInferenceAdapter",
    "trained_model_inference_adapter",
]
