"""Pinned media-processor execution for non-committing pipeline previews."""

from __future__ import annotations

import tempfile
from pathlib import Path

from foundry_lite.application.media_byte_verification import (
    MediaByteVerificationFailure,
    copy_verified_committed_media,
    raise_media_byte_domain_error,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_preview_port_types import (
    MediaItemVersionRecord,
    MediaProcessingRequest,
    MediaProcessingResult,
    MediaProcessorAdapter,
    MediaProcessorDescriptor,
    MediaProcessorRegistry,
    MediaStorageAdapter,
    ProcessorSpec,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


def invoke_preview_processor(
    processor: MediaProcessorAdapter,
    media_storage: MediaStorageAdapter,
    ctx: RequestContext,
    version: MediaItemVersionRecord,
    spec: ProcessorSpec,
) -> MediaProcessingResult:
    """Copy verified bytes into a sandbox and invoke the pinned processor."""

    spec_hash = _json_hash(_spec_payload(spec))
    with tempfile.TemporaryDirectory() as sandbox:
        source_path = Path(sandbox) / f"source.{version.format}"
        _copy_verified_media(media_storage, version, source_path)
        request = MediaProcessingRequest(
            tenant_id=ctx.tenant_id,
            media_item_version_id=version.media_item_version_id,
            blob_key=version.blob_key,
            spec=spec,
            processing_spec_hash=spec_hash,
            source_path=str(source_path),
            source_format=version.format,
            source_mime_type=version.sniffed_mime_type,
        )
        if not processor.supports(request):
            raise ValidationFailed(
                "media processor rejected the pinned preview request",
                details={"processorId": f"{spec.processor}@{spec.processor_version}"},
            )
        return processor.process(request)


def exact_processor_identity(processor_id: str) -> tuple[str, str]:
    """Parse an exact ``processor@version`` identity without silent fallback."""

    processor, marker, version = processor_id.partition("@")
    if not marker or not processor or not version:
        raise ValidationFailed(
            "processorId must pin an exact processor version",
            details={"processorId": processor_id, "expected": "processor@version"},
        )
    return processor, version


def processor_descriptor(
    registry: MediaProcessorRegistry,
    processor: str,
    version: str,
) -> MediaProcessorDescriptor:
    """Resolve only the exact registered processor descriptor."""

    descriptor = next(
        (item for item in registry.descriptors() if item.identity == (processor, version)),
        None,
    )
    if descriptor is None:
        raise ValidationFailed(
            "pinned media processor is not registered",
            details={"processor": processor, "processorVersion": version},
        )
    return descriptor


def _copy_verified_media(
    media_storage: MediaStorageAdapter,
    version: MediaItemVersionRecord,
    target: Path,
) -> None:
    try:
        with target.open("wb") as sink:
            copy_verified_committed_media(media_storage, version, sink)
    except MediaByteVerificationFailure as exc:
        raise_media_byte_domain_error(exc, operation="pipeline_preview")


def _spec_payload(spec: ProcessorSpec) -> JsonObject:
    return {
        "processor": spec.processor,
        "processorVersion": spec.processor_version,
        "model": spec.model,
        "modelVersion": spec.model_version,
        "parameters": dict(spec.parameters),
    }
