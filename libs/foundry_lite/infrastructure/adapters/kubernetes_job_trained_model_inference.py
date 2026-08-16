"""Trained-model inference adapter backed by the restricted Kubernetes Job broker."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace

from foundry_lite.infrastructure.adapters.container_code_execution_runtime import ContainerCommandRunner
from foundry_lite.infrastructure.adapters.container_trained_model_inference import (
    ContainerTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.container_trained_model_runtime import ContainerTrainedModelConfig
from foundry_lite.infrastructure.adapters.kubernetes_job_code_execution import (
    KubernetesJobBrokerCommandRunner,
    kubernetes_job_broker_config_from_env,
)


class KubernetesJobTrainedModelInferenceAdapter(ContainerTrainedModelInferenceAdapter):
    """Run each pinned model batch as a no-network, non-retrying Kubernetes Job."""

    profile_name = "kubernetes-job-trained-model"

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        command_runner: ContainerCommandRunner | None = None,
    ) -> None:
        source = os.environ if environ is None else environ
        broker_config = kubernetes_job_broker_config_from_env(source)
        runner = command_runner or KubernetesJobBrokerCommandRunner(broker_config)
        config = ContainerTrainedModelConfig.from_env(source, is_image_digest_required=True)
        config = replace(
            config,
            runtime_binary="kubernetes-job-client",
            workspace_root=broker_config.shared_workspace_root,
            is_image_digest_required=True,
        )
        super().__init__(
            config,
            command_runner=runner,
            environ={},
            is_image_digest_required=True,
        )
