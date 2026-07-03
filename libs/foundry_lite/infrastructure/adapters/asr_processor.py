"""Local ASR processor (Media/Content Plane M7, doc §7.2/§8.3).

Automatic speech recognition is a SEPARATE processor family from PDF raw text and OCR:
it produces a distinct ``asr_v1`` derivative (it never overwrites embedded text or OCR),
and its output is model-pinned because transcription is nondeterministic across model
versions. The ASR engine is injectable — the ``asr-whisper`` profile injects the real
faster-whisper engine (``_faster_whisper_asr_engine``, L2) while the in-process default
still raises ``asr_engine_unavailable`` so deterministic unit tests inject a fake and need
no model. Transcription runs in a worker thread bounded by a wall clock; a hung decode fails
closed (typed timeout), and an unsupported/undecodable audio is a typed validation failure.
Each segment carries its time code (``start_ms``/``end_ms``) and optional speaker/language.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
)

_DERIVATIVE_KIND = "asr_v1"
_DEFAULT_TIMEOUT_SECONDS = 300
# Cap on live worker threads. Whisper decodes in-process (ctranslate2 C code) and cannot be
# force-cancelled, so on timeout we abandon the worker; a SHARED bounded pool (not a fresh pool
# per call) means repeated timeouts reuse a fixed thread set instead of leaking one per timeout.
_MAX_WORKER_THREADS = 4


@dataclass(frozen=True)
class TranscriptSegment:
    """One time-coded transcript span returned by an ASR engine."""

    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None
    language: str | None = None


# source_path -> ordered transcript segments (one per utterance/window).
AsrEngine = Callable[[str], Sequence[TranscriptSegment]]


class AsrDocumentError(Exception):
    """Raised by an ASR engine when audio is undecodable, unsupported, or no model is bundled."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_asr_engine(source_path: str) -> list[TranscriptSegment]:
    # In-process default: no speech model is wired here, so deterministic unit tests inject a
    # fake and the seam stays covered. The real ``asr-whisper`` profile injects
    # ``_faster_whisper_asr_engine`` (L2) instead of relying on this default.
    del source_path
    raise AsrDocumentError("asr_engine_unavailable")


_whisper_model: Any | None = None


def _load_whisper_model() -> Any:
    # Build the tiny CPU model lazily/once (module-level cache) so repeated calls don't reload
    # the ~75MB weights. faster-whisper uses the ctranslate2 backend (no torch).
    global _whisper_model
    if _whisper_model is None:
        whisper_module = import_module("faster_whisper")
        _whisper_model = whisper_module.WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model


def _faster_whisper_asr_engine(source_path: str) -> list[TranscriptSegment]:
    # Real engine (L2): lazily import faster-whisper so the module has no import-time speech
    # dependency. ``language="en"`` is pinned (auto-detect misclassifies short clips). A
    # decode/load error is an undecodable-audio validation failure.
    try:
        model = _load_whisper_model()
        segments, _info = model.transcribe(source_path, language="en")
        return [
            TranscriptSegment(
                start_ms=round(segment.start * 1000),
                end_ms=round(segment.end * 1000),
                text=segment.text.strip(),
                language="en",
            )
            for segment in segments
        ]
    except Exception as exc:  # noqa: BLE001 - any decode/load error is an undecodable-audio validation failure
        raise AsrDocumentError("undecodable_audio") from exc


class AsrProcessorAdapter:
    """``MediaProcessorAdapter`` that transcribes audio into text (profile ``asr-whisper``)."""

    profile_name = "asr-whisper"

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS, asr_engine: AsrEngine | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._asr_engine = asr_engine or _default_asr_engine
        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKER_THREADS, thread_name_prefix="asr")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "Audio is undecodable or its codec is unsupported; it cannot be transcribed as-is.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "Transcription exceeded its time budget (possibly an over-long stream); retry bounded.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "asr_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "asr processor requires a sandbox source_path", request, is_retryable=False)
        segments = self._transcribe_within_timeout(request)
        units = tuple(
            ProcessedContentUnit(
                unit_kind="audio_segment",
                ordinal=index,
                text=segment.text,
                text_hash=hashlib.sha256(segment.text.encode("utf-8")).hexdigest(),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker=segment.speaker,
                language=segment.language,
            )
            for index, segment in enumerate(segments)
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=_content_hash(units),
            mime_type="text/plain",
            units=units,
        )

    def _transcribe_within_timeout(self, request: MediaProcessingRequest) -> Sequence[TranscriptSegment]:
        assert request.source_path is not None
        # Shared bounded pool: on timeout we abandon the worker (in-process whisper decode cannot
        # be cancelled) but reuse a fixed thread set, so repeated timeouts do not leak threads.
        future = self._executor.submit(self._asr_engine, request.source_path)
        try:
            return future.result(timeout=self._timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise self._error("timeout", "asr timed out", request, is_retryable=True) from exc
        except AsrDocumentError as exc:
            raise self._error("validation", exc.reason, request, is_retryable=False) from exc

    def _error(self, kind: str, reason: str, request: MediaProcessingRequest, *, is_retryable: bool) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"ASR processing failed: {reason}",
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS if kind == "timeout" else None,
                details={
                    "mediaItemVersionId": request.media_item_version_id,
                    "processorSpecHash": request.processing_spec_hash,
                    "reason": reason,
                },
            )
        )


def _content_hash(units: tuple[ProcessedContentUnit, ...]) -> str:
    digest = hashlib.sha256()
    for unit in units:
        digest.update(unit.text_hash.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
