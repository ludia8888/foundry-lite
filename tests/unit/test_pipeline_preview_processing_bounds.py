"""Bounded media preview request and evidence projection regression tests."""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.media_processor import MediaProcessingResult, ProcessedContentUnit
from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    ProcessorModelDescriptor,
    ProcessorPreviewCapability,
    ProcessorResourceRequirements,
)
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.pipeline_preview_executor import normalize_preview_limits
from foundry_lite.application.services.pipeline_preview_runtime import _derivative_payload
from foundry_lite.application.services.pipeline_preview_transforms import (
    _processor_parameters,
    bridge_or_output,
    use_llm,
)
from foundry_lite.domain.errors import ValidationFailed

_LIMITS: dict[str, object] = {
    "audioVideoSeconds": 60,
    "sceneCount": 12,
    "pdfPages": 3,
}


def test_asr_uses_canonical_duration_bound_below_server_cap() -> None:
    config = {
        "parameters": {
            "language": "en",
            "processingBounds": {"maxDurationMs": 999_999},
        },
        "processingBounds": {"maxDurationMs": 45_000, "maxSceneCount": 99},
    }

    parameters = _processor_parameters(config, _LIMITS, processor_id="asr_v1@1.0.0")

    assert parameters == {
        "language": "en",
        "requestedProcessingBounds": {"maxDurationMs": 45_000},
        "processingBounds": {"maxDurationMs": 45_000},
    }


def test_video_frames_and_vision_receive_duration_and_scene_bounds() -> None:
    config = {"processingBounds": {"maxDurationMs": 90_000, "maxSceneCount": 99}}

    frames = _processor_parameters(config, _LIMITS, processor_id="video_frames_v1@1.0.0")
    vision = _processor_parameters({}, _LIMITS, processor_id="video_vision_v1@1.0.0")

    assert frames["processingBounds"] == {
        "maxDurationMs": 60_000,
        "maxSceneCount": 12,
    }
    assert frames["requestedProcessingBounds"] == {
        "maxDurationMs": 90_000,
        "maxSceneCount": 99,
    }
    assert vision["processingBounds"] == {
        "maxDurationMs": 60_000,
        "maxSceneCount": 12,
    }
    assert vision["requestedProcessingBounds"] == {
        "maxDurationMs": 60_000,
        "maxSceneCount": 12,
    }


def test_video_probe_never_receives_processing_bounds() -> None:
    config = {
        "parameters": {
            "includeStreams": True,
            "processingBounds": {"maxDurationMs": 999_999, "maxSceneCount": 999},
        },
        "processingBounds": {"maxDurationMs": 5_000, "maxSceneCount": 2},
    }

    parameters = _processor_parameters(config, _LIMITS, processor_id="video_probe_v1@1.0.0")

    assert parameters == {"includeStreams": True}


@pytest.mark.parametrize("value", [0, -1, True, "60000"])
def test_media_processing_bound_must_be_a_positive_integer(value: object) -> None:
    config = {"processingBounds": {"maxDurationMs": value}}

    with pytest.raises(ValidationFailed, match="positive integer"):
        _processor_parameters(config, _LIMITS, processor_id="asr_v1@1.0.0")


def test_runtime_projects_processor_evidence_to_derivative_and_content_units() -> None:
    evidence = {
        "processingBounds": {"maxDurationMs": 45_000, "maxSceneCount": 5},
        "processedDurationMs": 38_400,
        "emittedSceneCount": 4,
    }
    result = MediaProcessingResult(
        media_item_version_id="version-1",
        processing_spec_hash="spec-1",
        derivative_kind="video_scene_frames",
        content_hash="content-1",
        units=(
            ProcessedContentUnit(
                unit_kind="video_frame",
                ordinal=0,
                text="Invoice approved",
                text_hash="text-1",
                start_ms=1300,
            ),
        ),
        processing_evidence=evidence,
    )

    payload = _derivative_payload(
        result,
        _version(),
        _descriptor(),
        {
            "mediaItemVersionId": "version-1",
            "mimeType": "video/mp4",
            "contentHash": "source-1",
            "sourceLocator": {},
        },
    )

    assert payload["processingEvidence"] == evidence
    units = payload["units"]
    assert isinstance(units, list)
    assert units[0]["processingEvidence"] == evidence


def test_use_llm_trial_count_bounds_model_calls_below_table_preview_limit() -> None:
    runtime = _SemanticRuntime({"trialCount": 2})
    node = {
        "id": "semantic",
        "kind": "transform",
        "descriptorId": "transform.use_llm",
        "specVersion": 1,
        "config": {"trialCount": 2},
    }
    inputs = {"input": [{"row": index} for index in range(8)]}

    rows = use_llm(node, inputs, {"tableRows": 8}, runtime)  # type: ignore[arg-type]

    assert rows == [{"row": 0}, {"row": 1}]
    assert runtime.seen_items == rows


def test_general_table_preview_defaults_and_clamps_to_500_rows() -> None:
    assert normalize_preview_limits(None)["tableRows"] == 500
    assert normalize_preview_limits({"tableRows": 999})["tableRows"] == 500
    assert normalize_preview_limits({"tableRows": 125})["tableRows"] == 125


def test_general_table_preview_returns_up_to_500_rows() -> None:
    node = {
        "id": "out",
        "kind": "output",
        "descriptorId": "output.dataset",
        "specVersion": 1,
        "config": {},
    }
    inputs = {"input": [{"row": index} for index in range(600)]}

    rows = bridge_or_output(node, inputs, {"tableRows": 500}, None)  # type: ignore[arg-type]

    assert len(rows) == 500
    assert rows[-1] == {"row": 499}


def test_use_llm_preview_is_capped_at_50_rows_when_table_preview_is_500() -> None:
    runtime = _SemanticRuntime({"trialCount": 500})
    node = {
        "id": "semantic",
        "kind": "transform",
        "descriptorId": "transform.use_llm",
        "specVersion": 1,
        "config": {"trialCount": 500},
    }
    inputs = {"input": [{"row": index} for index in range(500)]}

    rows = use_llm(node, inputs, {"tableRows": 500}, runtime)  # type: ignore[arg-type]

    assert len(rows) == 50
    assert rows == [{"row": index} for index in range(50)]
    assert runtime.seen_items == rows


class _SemanticRuntime:
    def __init__(self, expected_config: dict[str, object]) -> None:
        self.expected_config = expected_config
        self.seen_items: list[dict[str, object]] = []

    def interpret_semantics(
        self,
        items: list[dict[str, object]],
        *,
        config: dict[str, object],
        node_id: str,
        descriptor_id: str,
        spec_version: str,
        max_concurrency: int,
        request_timeout_seconds: int,
    ) -> list[dict[str, object]]:
        assert config == self.expected_config
        assert node_id == "semantic"
        assert descriptor_id == "transform.use_llm"
        assert spec_version == "1"
        assert max_concurrency in {2, 4}
        assert request_timeout_seconds == 30
        self.seen_items = [dict(item) for item in items]
        return self.seen_items


def _version() -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id="version-1",
        tenant_id="tenant-1",
        media_item_id="item-1",
        media_transaction_id="transaction-1",
        version_number=1,
        blob_key="media/video.mp4",
        content_hash="source-1",
        byte_size=1024,
        supplied_mime_type="video/mp4",
        sniffed_mime_type="video/mp4",
        schema_type="video",
        format="mp4",
        probe_metadata={},
        security_envelope={"classification": "confidential"},
        source_ref=None,
        status="COMMITTED",
        created_at="2026-07-16T00:00:00Z",
    )


def _descriptor() -> MediaProcessorDescriptor:
    return MediaProcessorDescriptor(
        processor="video_frames_v1",
        processor_version="1.0.0",
        adapter_profile="video-scene-frames",
        input_formats=("mp4",),
        output_kinds=("video_scene_frames",),
        model=ProcessorModelDescriptor("tesseract", "5.5.2"),
        resources=ProcessorResourceRequirements(1, 512),
        preview=ProcessorPreviewCapability("bounded", max_media_items=5, max_duration_seconds=60),
    )
