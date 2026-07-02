"""ASR processor adapter (Media/Content Plane M7).

The ASR engine is injected (a deterministic fake), so these run with no speech model.
Cover the contract: deterministic time-coded segments with per-segment text hashes,
supports() gating, typed validation (undecodable) and fail-closed timeout, and the
failure taxonomy.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.asr_processor import (
    AsrDocumentError,
    AsrProcessorAdapter,
    TranscriptSegment,
    _default_asr_engine,
)


def _request(*, processor: str = "asr_v1") -> MediaProcessingRequest:
    spec = ProcessorSpec(processor=processor, processor_version="1.0.0", model="whisper", model_version="large-v3")
    return MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="spec-1",
        source_path="/sandbox/audio.wav",
    )


def _segments(_path: str) -> Sequence[TranscriptSegment]:
    return [
        TranscriptSegment(start_ms=0, end_ms=1500, text="hello there", speaker="spk_0", language="en"),
        TranscriptSegment(start_ms=1500, end_ms=3200, text="general kenobi", speaker="spk_1", language="en"),
    ]


def test_asr_extracts_time_coded_segments_deterministically() -> None:
    adapter = AsrProcessorAdapter(asr_engine=_segments)
    first = adapter.process(_request())
    second = adapter.process(_request())

    assert first.derivative_kind == "asr_v1"
    assert [u.text for u in first.units] == ["hello there", "general kenobi"]
    assert [u.unit_kind for u in first.units] == ["audio_segment", "audio_segment"]
    assert (first.units[0].start_ms, first.units[0].end_ms) == (0, 1500)
    assert first.units[1].speaker == "spk_1"
    assert first.units[0].language == "en"
    assert first.content_hash == second.content_hash
    assert [u.text_hash for u in first.units] == [u.text_hash for u in second.units]


def test_supports_only_asr_processor() -> None:
    adapter = AsrProcessorAdapter(asr_engine=_segments)
    assert adapter.supports(_request()) is True
    assert adapter.supports(_request(processor="ocr_v1")) is False


def test_failure_contract_declares_validation_and_timeout() -> None:
    contract = AsrProcessorAdapter(asr_engine=_segments).failure_contract()
    assert contract.adapter_profile == "asr-whisper"
    assert {mode.kind for mode in contract.modes} >= {"validation", "timeout"}


def test_undecodable_audio_is_validation_failure() -> None:
    def _bad(_path: str) -> Sequence[TranscriptSegment]:
        raise AsrDocumentError("unsupported_codec")

    adapter = AsrProcessorAdapter(asr_engine=_bad)
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request())
    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False


def test_timeout_fails_closed() -> None:
    release = threading.Event()

    def _blocking(_path: str) -> Sequence[TranscriptSegment]:
        release.wait(timeout=10)
        return [TranscriptSegment(start_ms=0, end_ms=1, text="never")]

    adapter = AsrProcessorAdapter(timeout_seconds=0, asr_engine=_blocking)
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.process(_request())
        assert excinfo.value.failure.kind == "timeout"
        assert excinfo.value.failure.is_retryable is True
    finally:
        release.set()


def test_process_requires_a_sandbox_source_path() -> None:
    adapter = AsrProcessorAdapter(asr_engine=_segments)
    request = MediaProcessingRequest(
        tenant_id="t", media_item_version_id="miv-1", blob_key="b", spec=_request().spec, processing_spec_hash="s"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(request)
    assert excinfo.value.failure.kind == "validation"


def test_default_engine_without_a_bundled_speech_model_is_a_validation_failure() -> None:
    # No speech model is bundled for CI/local; the default raises a typed validation error
    # (a real profile injects an engine — live ASR is deferred).
    with pytest.raises(AsrDocumentError) as excinfo:
        _default_asr_engine("/sandbox/audio.wav")
    assert excinfo.value.reason == "asr_engine_unavailable"


def test_repeated_timeouts_do_not_accumulate_worker_threads() -> None:
    # A per-call ThreadPoolExecutor abandoned on timeout leaks a live thread per timeout;
    # repeated timeouts must NOT grow the thread count unboundedly.
    release = threading.Event()

    def _blocking(_path: str) -> Sequence[TranscriptSegment]:
        release.wait(timeout=10)
        return []

    adapter = AsrProcessorAdapter(timeout_seconds=0, asr_engine=_blocking)
    baseline = threading.active_count()
    try:
        for _ in range(20):
            with pytest.raises(AdapterError) as excinfo:
                adapter.process(_request())
            assert excinfo.value.failure.kind == "timeout"
        assert threading.active_count() - baseline <= 8
    finally:
        release.set()
