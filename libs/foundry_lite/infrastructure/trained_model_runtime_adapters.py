"""Concrete trained-model adapters exposed as one runtime composition bundle."""

from foundry_lite.infrastructure.adapters.container_trained_model_inference import (
    ContainerTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.local_trained_model_inference import (
    LocalTrainedModelInferenceAdapter,
)

__all__ = [
    "ContainerTrainedModelInferenceAdapter",
    "LocalTrainedModelInferenceAdapter",
]
