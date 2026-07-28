"""Discoverable Pipeline Builder node and media-processor catalogs."""

from __future__ import annotations

from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    MediaProcessorRegistry,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_model import pipeline_node_descriptor_payloads
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    trained_model_definition_payload,
)
from foundry_lite.domain.context import RequestContext


class PipelineCatalogService(CoreService):
    """Expose server-owned, versioned builder capabilities without UI hard-coding."""

    required_dependencies = (
        "policy",
        "media_processor_registry",
        "trained_model_inference_port",
    )
    required_collaborators = ()
    media_processor_registry: MediaProcessorRegistry | None

    def node_types(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        items = pipeline_node_descriptor_payloads()
        return {"schemaVersion": 2, "items": items, "count": len(items)}

    def media_processors(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        registry = self.media_processor_registry
        if registry is None:
            return {
                "available": False,
                "reason": "media processor registry is not configured for this runtime",
                "items": [],
            }
        items = [_processor_payload(descriptor) for descriptor in registry.descriptors()]
        return {"available": True, "profile": registry.profile_name, "items": items, "count": len(items)}

    def trained_models(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        items = [trained_model_definition_payload(model) for model in self.trained_model_inference_port.list_models()]
        return {"available": True, "items": items, "count": len(items)}


def _processor_payload(descriptor: MediaProcessorDescriptor) -> dict[str, object]:
    return {
        "processorId": f"{descriptor.processor}@{descriptor.processor_version}",
        "processor": descriptor.processor,
        "processorVersion": descriptor.processor_version,
        "adapterProfile": descriptor.adapter_profile,
        "inputFormats": list(descriptor.input_formats),
        "outputKinds": list(descriptor.output_kinds),
        "parameterSchema": dict(descriptor.parameter_schema),
        "model": {"name": descriptor.model.name, "version": descriptor.model.version},
        "resources": {
            "cpuCores": descriptor.resources.cpu_cores,
            "memoryMb": descriptor.resources.memory_mb,
            "accelerator": descriptor.resources.accelerator,
            "timeoutSeconds": descriptor.resources.timeout_seconds,
        },
        "preview": {
            "mode": descriptor.preview.mode,
            "maxMediaItems": descriptor.preview.max_media_items,
            "maxDurationSeconds": descriptor.preview.max_duration_seconds,
        },
        "deterministic": descriptor.is_deterministic,
    }
