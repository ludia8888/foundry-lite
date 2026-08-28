"""Pinned media-processor execution for non-committing pipeline previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from foundry_lite.application.media_source_materialization import (
    MediaByteVerificationFailure,
    materialized_verified_media,
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
    MediaSourceWorkspace,
    MediaStorageAdapter,
    ProcessorSpec,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


def invoke_preview_processor(
    processor: MediaProcessorAdapter,
    media_storage: MediaStorageAdapter,
    media_source_workspace: MediaSourceWorkspace,
    ctx: RequestContext,
    version: MediaItemVersionRecord,
    spec: ProcessorSpec,
) -> MediaProcessingResult:
    """Copy verified bytes into a sandbox and invoke the pinned processor."""

    spec_hash = _json_hash(_spec_payload(spec))
    try:
        workspace = materialized_verified_media(
            media_source_workspace,
            media_storage,
            version,
            file_name=f"source.{version.format}",
        )
        with workspace as source_path:
            return _invoke_materialized_processor(processor, ctx, version, spec, spec_hash, source_path)
    except MediaByteVerificationFailure as exc:
        raise_media_byte_domain_error(exc, operation="pipeline_preview")


def _invoke_materialized_processor(
    processor: MediaProcessorAdapter,
    ctx: RequestContext,
    version: MediaItemVersionRecord,
    spec: ProcessorSpec,
    spec_hash: str,
    source_path: str,
) -> MediaProcessingResult:
    request = MediaProcessingRequest(
        tenant_id=ctx.tenant_id,
        media_item_version_id=version.media_item_version_id,
        blob_key=version.blob_key,
        spec=spec,
        processing_spec_hash=spec_hash,
        source_path=source_path,
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


def version_with_source_security(
    version: MediaItemVersionRecord,
    item: Mapping[str, object],
) -> MediaItemVersionRecord:
    """Bind preview-supplied security evidence to the exact committed version."""

    envelope = item.get("securityEnvelope")
    if not isinstance(envelope, Mapping):
        raise ValidationFailed(
            "media preview source security envelope is missing",
            details={"mediaItemVersionId": version.media_item_version_id},
        )
    return replace(
        version,
        security_envelope={str(key): value for key, value in envelope.items()},
        has_legal_hold=bool(envelope.get("hasLegalHold", version.has_legal_hold)),
    )


def require_total_media_bytes(versions: Sequence[MediaItemVersionRecord], byte_limit: int) -> None:
    """Reject a media preview selection that exceeds its bounded byte budget."""

    total = sum(version.byte_size for version in versions)
    if total > byte_limit:
        raise ValidationFailed(
            "media preview exceeds the byte budget",
            details={"totalBytes": total, "byteLimit": byte_limit},
        )


def _spec_payload(spec: ProcessorSpec) -> JsonObject:
    return {
        "processor": spec.processor,
        "processorVersion": spec.processor_version,
        "model": spec.model,
        "modelVersion": spec.model_version,
        "parameters": dict(spec.parameters),
    }
