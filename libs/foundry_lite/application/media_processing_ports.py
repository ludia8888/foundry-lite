"""Media processing port types shared across application composition modules."""

from foundry_lite.application.ports.media_derivative_repository import (
    ContentUnitRecord,
    MediaDerivativeRecord,
    MediaDerivativeRepository,
    MediaProcessingRunRecord,
)
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    MediaProcessorAdapter,
    ProcessorSpec,
)
from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    MediaProcessorRegistry,
)
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaRepository,
    MediaSetRecord,
    MediaSetSelectionRecord,
)
from foundry_lite.application.ports.media_source_workspace import MediaSourceWorkspace
from foundry_lite.application.ports.media_storage import MediaStorageAdapter

__all__ = [
    "ContentUnitRecord",
    "MediaDerivativeRecord",
    "MediaDerivativeRepository",
    "MediaItemVersionRecord",
    "MediaProcessingRequest",
    "MediaProcessingResult",
    "MediaProcessingRunRecord",
    "MediaProcessorAdapter",
    "MediaProcessorDescriptor",
    "MediaProcessorRegistry",
    "MediaRepository",
    "MediaSetRecord",
    "MediaSetSelectionRecord",
    "MediaSourceWorkspace",
    "MediaStorageAdapter",
    "ProcessorSpec",
]
