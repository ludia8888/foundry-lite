"""Application port for exact, discoverable media processor resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter, ProcessorSpec

ProcessorPreviewMode = Literal["none", "bounded"]


def _empty_parameter_schema() -> Mapping[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True)
class ProcessorModelDescriptor:
    """Model or executable identity exposed to builders and deployment pinning."""

    name: str
    version: str


@dataclass(frozen=True)
class ProcessorResourceRequirements:
    """Minimum scheduling envelope for one processor invocation."""

    cpu_cores: float
    memory_mb: int
    accelerator: str = "cpu"
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ProcessorPreviewCapability:
    """Bounded preview capability advertised independently from full execution."""

    mode: ProcessorPreviewMode
    max_media_items: int = 0
    max_duration_seconds: int | None = None


@dataclass(frozen=True)
class MediaProcessorDescriptor:
    """Discoverable, version-pinned contract for one registry entry."""

    processor: str
    processor_version: str
    adapter_profile: str
    input_formats: tuple[str, ...]
    output_kinds: tuple[str, ...]
    model: ProcessorModelDescriptor
    resources: ProcessorResourceRequirements
    preview: ProcessorPreviewCapability
    parameter_schema: Mapping[str, object] = field(default_factory=_empty_parameter_schema)
    is_deterministic: bool = True

    @property
    def identity(self) -> tuple[str, str]:
        return (self.processor, self.processor_version)


@dataclass(frozen=True)
class MediaProcessorRegistration:
    """One descriptor paired with the adapter that implements it."""

    descriptor: MediaProcessorDescriptor
    adapter: MediaProcessorAdapter


class MediaProcessorRegistry(Protocol):
    """Resolve processors by exact identity without profile or version fallback."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the stable resolution failure taxonomy."""
        ...

    def descriptors(self) -> tuple[MediaProcessorDescriptor, ...]:
        """Return deterministic catalog entries available in this runtime."""
        ...

    def resolve(
        self,
        spec: ProcessorSpec,
        *,
        input_format: str | None = None,
    ) -> MediaProcessorAdapter:
        """Resolve an exact processor/version and validate its declared input format."""
        ...
