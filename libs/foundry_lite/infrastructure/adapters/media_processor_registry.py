"""Static media processor registry used by local and protected runtimes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter, ProcessorSpec
from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    MediaProcessorRegistration,
    MediaProcessorRegistry,
    ProcessorModelDescriptor,
    ProcessorPreviewCapability,
    ProcessorResourceRequirements,
)
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.infrastructure.adapters.media_processor_av_adapters import (
    default_av_processor_adapters,
)
from foundry_lite.infrastructure.adapters.media_processor_document_adapters import (
    default_document_processor_adapters,
    default_pdf_ocr_model_version,
)

_REGISTRY_PROFILE = "media-processor-registry"
_COMPATIBLE_V1_IDENTITIES = ("1", "1.0", "1.0.0")


def build_default_media_processor_registry(
    vision_embedding_model: VisionEmbeddingModelAdapter,
) -> MediaProcessorRegistry:
    """Compose every currently shipped processor into one runtime registry."""

    adapters = _default_adapters(vision_embedding_model)
    registrations: list[MediaProcessorRegistration] = []
    for adapter, descriptor in zip(adapters, _default_descriptors(), strict=True):
        registrations.extend(_versioned_registrations(adapter, descriptor))
    return StaticMediaProcessorRegistry(registrations)


def _default_adapters(
    vision_embedding_model: VisionEmbeddingModelAdapter,
) -> tuple[MediaProcessorAdapter, ...]:
    return (
        *default_document_processor_adapters(),
        *default_av_processor_adapters(vision_embedding_model),
    )


def _default_descriptors() -> tuple[MediaProcessorDescriptor, ...]:
    return (
        _descriptor("pdf_text_v1", "pdf-pypdf", ("pdf",), ("pdf_text",), "pypdf", "runtime", 1, 512, 30),
        _descriptor(
            "pdf_layout_v1",
            "pdf-layout-pypdf",
            ("pdf",),
            ("pdf_layout",),
            "pypdf-layout-heuristic",
            "font-position-v1",
            1,
            512,
            30,
        ),
        _descriptor(
            "pdf_ocr_v1",
            "pdf-ocr-tesseract-poppler",
            ("pdf",),
            ("pdf_ocr",),
            "tesseract+poppler",
            default_pdf_ocr_model_version(),
            4,
            2048,
            120,
        ),
        _descriptor("ocr_v1", "ocr-tesseract", _IMAGE_FORMATS, ("ocr_v1",), "tesseract", "runtime", 2, 1024, 60),
        _descriptor("image_v1", "image-pillow", _IMAGE_FORMATS, ("thumbnail",), "pillow", "runtime", 1, 512, 30),
        _descriptor("asr_v1", "asr-whisper", _AUDIO_FORMATS, ("asr_v1",), "whisper", "tiny", 4, 4096, 300),
        _descriptor("video_probe_v1", "ffprobe", _VIDEO_FORMATS, ("video_probe",), "ffprobe", "runtime", 1, 512, 30),
        _descriptor(
            "video_frames_v1",
            "video-scene-frames",
            _VIDEO_FORMATS,
            ("video_scene_frames",),
            "ffmpeg+tesseract",
            "runtime",
            4,
            2048,
            300,
        ),
        _descriptor(
            "video_vision_v1",
            "video-scene-vision",
            _VIDEO_FORMATS,
            ("video_scene_vision",),
            "clip-ViT-B-32",
            "runtime",
            4,
            4096,
            300,
            accelerator="gpu-optional",
            is_deterministic=False,
        ),
    )


_IMAGE_FORMATS = ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp")
_AUDIO_FORMATS = ("wav", "mp3", "m4a", "flac", "ogg", "webm", "mp4")
_VIDEO_FORMATS = ("mp4", "mov", "mkv", "webm", "avi", "m4v")


def _descriptor(
    processor: str,
    adapter_profile: str,
    input_formats: tuple[str, ...],
    output_kinds: tuple[str, ...],
    model_name: str,
    model_version: str,
    cpu_cores: float,
    memory_mb: int,
    timeout_seconds: int,
    *,
    accelerator: str = "cpu",
    is_deterministic: bool = True,
) -> MediaProcessorDescriptor:
    parameter_schema = _processor_parameter_schema(processor)
    return MediaProcessorDescriptor(
        processor=processor,
        processor_version=_COMPATIBLE_V1_IDENTITIES[0],
        adapter_profile=adapter_profile,
        input_formats=input_formats,
        output_kinds=output_kinds,
        parameter_schema=parameter_schema,
        model=ProcessorModelDescriptor(model_name, model_version),
        resources=ProcessorResourceRequirements(cpu_cores, memory_mb, accelerator, timeout_seconds),
        preview=ProcessorPreviewCapability("bounded", max_media_items=5, max_duration_seconds=60),
        is_deterministic=is_deterministic,
    )


def _versioned_registrations(
    adapter: MediaProcessorAdapter,
    descriptor: MediaProcessorDescriptor,
) -> tuple[MediaProcessorRegistration, ...]:
    return tuple(
        MediaProcessorRegistration(
            descriptor=MediaProcessorDescriptor(
                processor=descriptor.processor,
                processor_version=version,
                adapter_profile=descriptor.adapter_profile,
                input_formats=descriptor.input_formats,
                output_kinds=descriptor.output_kinds,
                model=descriptor.model,
                resources=descriptor.resources,
                preview=descriptor.preview,
                parameter_schema=descriptor.parameter_schema,
                is_deterministic=descriptor.is_deterministic,
            ),
            adapter=adapter,
        )
        for version in _COMPATIBLE_V1_IDENTITIES
    )


def _empty_object_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _processor_parameter_schema(processor: str) -> dict[str, object]:
    if processor == "pdf_ocr_v1":
        return _pdf_ocr_parameter_schema()
    if processor in {"pdf_text_v1", "pdf_layout_v1"}:
        return _pdf_parameter_schema()
    return _empty_object_schema()


def _pdf_parameter_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "maxPages": {"type": "integer", "minimum": 1, "maximum": 10000},
            "pageSelection": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def _pdf_ocr_parameter_schema() -> dict[str, object]:
    schema = _pdf_parameter_schema()
    raw_properties = schema.get("properties")
    properties = (
        {str(key): value for key, value in raw_properties.items()} if isinstance(raw_properties, Mapping) else {}
    )
    properties.update(
        {
            "dpi": {"type": "integer", "minimum": 72, "maximum": 300},
            "languages": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "pattern": "^[A-Za-z0-9_.+-]+$",
            },
        }
    )
    return {**schema, "properties": properties}


class StaticMediaProcessorRegistry:
    """Immutable exact-match registry assembled during runtime startup."""

    profile_name = _REGISTRY_PROFILE

    def __init__(self, registrations: Iterable[MediaProcessorRegistration]) -> None:
        entries: dict[tuple[str, str], MediaProcessorRegistration] = {}
        for registration in registrations:
            _validate_registration(registration)
            identity = registration.descriptor.identity
            if identity in entries:
                raise ValueError(f"duplicate media processor registration: {identity[0]}@{identity[1]}")
            entries[identity] = registration
        self._entries = entries

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="resolve",
                    kind="unsupported",
                    is_retryable=False,
                    operator_message="The requested processor identity or input format is not registered.",
                ),
            ),
        )

    def descriptors(self) -> tuple[MediaProcessorDescriptor, ...]:
        return tuple(registration.descriptor for registration in self._entries.values())

    def resolve(
        self,
        spec: ProcessorSpec,
        *,
        input_format: str | None = None,
    ) -> MediaProcessorAdapter:
        registration = self._entries.get((spec.processor, spec.processor_version))
        if registration is None:
            raise _resolution_error(spec, input_format, reason="processor_version_not_registered")
        if not _accepts_input_format(registration.descriptor, input_format):
            raise _resolution_error(spec, input_format, reason="input_format_not_supported")
        return registration.adapter


def _validate_registration(registration: MediaProcessorRegistration) -> None:
    descriptor = registration.descriptor
    if not descriptor.processor or not descriptor.processor_version:
        raise ValueError("media processor registration requires processor and processor_version")
    if descriptor.adapter_profile != registration.adapter.profile_name:
        raise ValueError(
            "media processor descriptor adapter_profile does not match adapter: "
            f"{descriptor.adapter_profile} != {registration.adapter.profile_name}"
        )
    if not descriptor.input_formats or not descriptor.output_kinds:
        raise ValueError(f"media processor descriptor requires input/output kinds: {descriptor.identity}")


def _accepts_input_format(descriptor: MediaProcessorDescriptor, input_format: str | None) -> bool:
    if input_format is None:
        return True
    normalized = input_format.strip().lower()
    accepted = {candidate.strip().lower() for candidate in descriptor.input_formats}
    return "*" in accepted or normalized in accepted


def _resolution_error(spec: ProcessorSpec, input_format: str | None, *, reason: str) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile=_REGISTRY_PROFILE,
            operation="resolve",
            kind="unsupported",
            is_retryable=False,
            operator_message="requested media processor is unavailable for the pinned identity and input format",
            details={
                "processor": spec.processor,
                "processorVersion": spec.processor_version,
                "inputFormat": input_format,
                "reason": reason,
            },
        )
    )
