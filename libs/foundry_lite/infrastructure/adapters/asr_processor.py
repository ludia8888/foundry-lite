"""Local ASR processor (Media/Content Plane M7, doc §7.2/§8.3).

Automatic speech recognition is a SEPARATE processor family from PDF raw text and OCR:
it produces a distinct ``asr_v1`` derivative (it never overwrites embedded text or OCR),
and its output is model-pinned because transcription is nondeterministic across model
versions. The ASR engine is injectable — the default raises ``asr_engine_unavailable``
because no speech model is bundled (a real profile injects Whisper/faster-whisper; live ASR
is deferred like live-OCR / live-ES), so tests inject a deterministic fake and need no model.
Transcription runs in a worker thread bounded by a wall clock; a hung decode fails closed
(typed timeout), and an unsupported/undecodable audio is a typed validation failure. Each
segment carries its time code (``start_ms``/``end_ms``) and optional speaker/language.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

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
    # No speech model is bundled (no Whisper weights in CI); a real profile injects one.
    # Shipping the adapter + contract now and deferring the live engine mirrors the
    # live-OCR / live-ES deferral — the injectable seam is what M7 proves.
    del source_path
    raise AsrDocumentError("asr_engine_unavailable")


class AsrProcessorAdapter:
    """``MediaProcessorAdapter`` that transcribes audio into text (profile ``asr-whisper``)."""

    profile_name = "asr-whisper"

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS, asr_engine: AsrEngine | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._asr_engine = asr_engine or _default_asr_engine

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
        # Not a context manager: on timeout we abandon the worker (shutdown wait=False)
        # rather than block on shutdown(wait=True) for a hung transcription call.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._asr_engine, request.source_path)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "asr timed out", request, is_retryable=True) from exc
        except AsrDocumentError as exc:
            pool.shutdown(wait=False)
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
