"""Local video-probe processor (Media/Content Plane M6, doc §7/§8).

Probes a video's container/stream metadata (duration, container, codec, resolution) via an
injectable probe runner, in a worker thread bounded by a wall clock so a hung probe fails
closed (typed timeout). The default runner raises ``probe_engine_unavailable`` because no
ffprobe binary is bundled in CI; a real profile injects a subprocess runner that SIGTERMs its
process group on timeout (live transcode/HLS is deferred), mirroring the live-OCR deferral —
so tests inject a deterministic fake and need no system binary. A probe that cannot read the
stream is a typed validation failure, never a partial result. Reads only a sandbox path.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess  # nosec B404 - fixed ffprobe arg list, no shell, sandbox path only
from collections.abc import Callable
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

_DERIVATIVE_KIND = "video_probe"
_DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class VideoProbe:
    """Container/stream metadata a ``video_probe`` derivative pins."""

    duration_seconds: float
    container_format: str
    video_codec: str
    width: int
    height: int
    has_audio: bool = False


# source_path -> probe (raises VideoProbeError when the stream is unreadable/unavailable).
VideoProbeRunner = Callable[[str], VideoProbe]


class VideoProbeError(Exception):
    """Raised by a probe runner when a video stream is unreadable or no engine is available."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_probe_runner(source_path: str) -> VideoProbe:
    # No ffprobe binary is bundled in CI; a real profile injects a subprocess runner that
    # SIGTERMs its process group on timeout. Live video probe is deferred like live OCR.
    del source_path
    raise VideoProbeError("probe_engine_unavailable")


_FFPROBE_TIMEOUT_SECONDS = 25


def _ffprobe_video_probe_runner(source_path: str) -> VideoProbe:
    # Real engine (L3): run system ``ffprobe`` over a sandbox path with a fixed arg list
    # (no shell) and a wall-clock timeout. On timeout the whole process group is SIGTERMed
    # (M-T3-002 process-group-kill) so a hung ffprobe never leaks; a non-zero exit or
    # unreadable stream is an unprobeable-media validation failure (the adapter types it).
    output = _run_ffprobe(source_path)
    return _parse_ffprobe_json(output)


def _run_ffprobe(source_path: str) -> str:
    args = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", source_path]
    process = subprocess.Popen(  # nosec B603 B607 - fixed ffprobe arg list, no shell, sandbox path only
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        stdout, _stderr = process.communicate(timeout=_FFPROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait()
        raise VideoProbeError("probe_timed_out") from exc
    if process.returncode != 0:
        raise VideoProbeError("unprobeable_media")
    return stdout.decode("utf-8", errors="replace")


def _parse_ffprobe_json(output: str) -> VideoProbe:
    try:
        payload = json.loads(output)
        streams = payload["streams"]
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
        return VideoProbe(
            duration_seconds=float(payload["format"]["duration"]),
            container_format=str(payload["format"]["format_name"]),
            video_codec=str(video["codec_name"]),
            width=int(video["width"]),
            height=int(video["height"]),
            has_audio=has_audio,
        )
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError) as exc:
        raise VideoProbeError("unprobeable_media") from exc


class VideoProbeProcessorAdapter:
    """``MediaProcessorAdapter`` that probes video container/stream metadata (profile ``ffprobe``)."""

    profile_name = "ffprobe"

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        probe_runner: VideoProbeRunner | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._probe_runner = probe_runner or _default_probe_runner

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "Video stream is unreadable or no probe engine is available; it cannot be probed as-is.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "Video probe exceeded its time budget; the probe process is killed and retry is bounded.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "video_probe_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "video probe requires a sandbox source_path", request, is_retryable=False)
        probe = self._probe_within_timeout(request)
        text = _probe_text(probe)
        unit = ProcessedContentUnit(
            unit_kind="video",
            ordinal=0,
            text=text,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=unit.text_hash,
            mime_type="application/json",
            units=(unit,),
        )

    def _probe_within_timeout(self, request: MediaProcessingRequest) -> VideoProbe:
        assert request.source_path is not None
        # Not a context manager: on timeout we abandon the worker (shutdown wait=False)
        # rather than block on shutdown(wait=True) for a hung probe subprocess.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._probe_runner, request.source_path)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "video probe timed out", request, is_retryable=True) from exc
        except VideoProbeError as exc:
            pool.shutdown(wait=False)
            raise self._error("validation", exc.reason, request, is_retryable=False) from exc

    def _error(self, kind: str, reason: str, request: MediaProcessingRequest, *, is_retryable: bool) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"Video probe failed: {reason}",
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS if kind == "timeout" else None,
                details={
                    "mediaItemVersionId": request.media_item_version_id,
                    "processorSpecHash": request.processing_spec_hash,
                    "reason": reason,
                },
            )
        )


def _probe_text(probe: VideoProbe) -> str:
    return (
        f"container={probe.container_format} codec={probe.video_codec} "
        f"duration={probe.duration_seconds} width={probe.width} height={probe.height} "
        f"has_audio={probe.has_audio}"
    )
