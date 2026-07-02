"""Local video-probe + scene-frame OCR processors (Media/Content Plane M6/L10, doc §7/§8).

Probes a video's container/stream metadata (duration, container, codec, resolution) via an
injectable probe runner, in a worker thread bounded by a wall clock so a hung probe fails
closed (typed timeout). The default runner raises ``probe_engine_unavailable`` because no
ffprobe binary is bundled in CI; a real profile injects a subprocess runner that SIGTERMs its
process group on timeout (live transcode/HLS is deferred), mirroring the live-OCR deferral —
so tests inject a deterministic fake and need no system binary. A probe that cannot read the
stream is a typed validation failure, never a partial result. Reads only a sandbox path.

L10 adds VISUAL text: ``VideoSceneFrameProcessorAdapter`` (profile ``video-scene-frames``,
processor ``video_frames_v1``) extracts **scene frames** from a video (Foundry's video→media
``extract_scene_frames`` primitive: a media→media transform producing a derived image set,
each frame pinned to the source version + carrying a timestamp/sceneScore) and OCRs each frame
(``imageOcrV1``, media→tabular) so on-screen text becomes searchable content. The frame
extraction is an injectable seam whose default raises ``frame_extractor_unavailable``; the
real extractor runs ffmpeg scene-select + showinfo timestamps then Tesseract per frame. The
``scene_sensitivity`` (MORE_SENSITIVE|STANDARD|LESS_SENSITIVE) maps to the ffmpeg scene
threshold. A corrupt/undecodable video is a typed ``unprocessable_video`` validation failure;
a hung extraction fails closed as a typed timeout. The derived frames live off the immutable
source version (never the source) and inherit the source security envelope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess  # nosec B404 - fixed ffmpeg/ffprobe arg list, no shell, sandbox path only
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
)
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.infrastructure.adapters.ocr_processor import _tesseract_ocr_engine

_DERIVATIVE_KIND = "video_probe"
_DEFAULT_TIMEOUT_SECONDS = 30
# Minimal ffmpeg/ffprobe input-protocol allow-list: only local file access (+ crypto for
# AES-encrypted containers). Blocks http/https/tcp/... so a crafted container cannot turn a
# probe into a local-file read (LFI) or a request to a network endpoint (SSRF).
_PROTOCOL_ALLOWLIST_ARGS = ("-protocol_whitelist", "file,crypto")


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
    args = [
        "ffprobe",
        "-v",
        "quiet",
        # LFI/SSRF guard: an untrusted container (concat/playlist) can reference file:// or
        # http:// (e.g. cloud metadata) sub-inputs; restrict to local file (+crypto) protocols
        # so only the sandbox path itself can be opened. ffprobe does not support ffmpeg's
        # -nostdin flag, so stdin is closed at the process boundary below.
        *_PROTOCOL_ALLOWLIST_ARGS,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        source_path,
    ]
    process = subprocess.Popen(  # nosec B603 B607 - fixed ffprobe arg list, no shell, sandbox path only
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
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


_SCENE_FRAMES_DERIVATIVE = "video_scene_frames"
_FRAMES_PROCESSOR = "video_frames_v1"
_FRAME_TIMEOUT_SECONDS = 120
_FFMPEG_TIMEOUT_SECONDS = 90
# scene_sensitivity -> ffmpeg scene-detection threshold (lower threshold => more frames).
_SCENE_THRESHOLDS = {"MORE_SENSITIVE": 0.1, "STANDARD": 0.3, "LESS_SENSITIVE": 0.5}
_DEFAULT_SENSITIVITY = "STANDARD"

# source_path -> one (start_ms, ocr_text) per scene frame with recognized text.
SceneFrameExtractor = Callable[[str], list[tuple[int, str]]]
# (source_path, outdir) -> one (start_ms, frame_image_path) per scene frame written into outdir
# (L11 visual embedding input). The caller owns outdir so frames outlive extraction for embedding.
SceneFramePathExtractor = Callable[[str, str], list[tuple[int, str]]]


class VideoFrameError(Exception):
    """Raised by a scene-frame extractor when a video is unprocessable or no engine exists."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_scene_frame_extractor(source_path: str) -> list[tuple[int, str]]:
    # No ffmpeg/Tesseract is wired in-process; deterministic unit tests inject a fake and the
    # seam stays covered. The real ``video-scene-frames`` profile injects the ffmpeg extractor.
    del source_path
    raise VideoFrameError("frame_extractor_unavailable")


def _scene_threshold(scene_sensitivity: str) -> float:
    return _SCENE_THRESHOLDS.get(scene_sensitivity, _SCENE_THRESHOLDS[_DEFAULT_SENSITIVITY])


def _ffmpeg_scene_frame_extractor(
    source_path: str, *, scene_sensitivity: str = "MORE_SENSITIVE"
) -> list[tuple[int, str]]:
    # Real engine (L10): extract scene frames with ffmpeg's ``select=...scene`` filter into a
    # temp dir, one PNG per scene frame; ``showinfo`` prints ``pts_time:<seconds>`` to STDERR in
    # frame order (so we keep the info log level, never ``-loglevel error``). Each frame is OCR'd
    # via the existing Tesseract engine; only frames with recognized text are returned.
    threshold = _scene_threshold(scene_sensitivity)
    with tempfile.TemporaryDirectory() as outdir:
        timecodes = _run_ffmpeg_scene_select(source_path, threshold, outdir)
        frames = sorted(Path(outdir).glob("f_*.png"))
        if not frames:
            return []
        recognized: list[tuple[int, str]] = []
        for index, frame in enumerate(frames):
            pts = timecodes[index] if index < len(timecodes) else 0.0
            text = "\n".join(_tesseract_ocr_engine(str(frame))).strip()
            if text:
                recognized.append((round(pts * 1000), text))
        return recognized


def _run_ffmpeg_scene_select(source_path: str, threshold: float, outdir: str) -> list[float]:
    select = f"select='eq(n,0)+gt(scene,{threshold})',showinfo"
    args = [
        "ffmpeg",
        "-hide_banner",
        # LFI/SSRF guard (same as ffprobe): pin an input-protocol allow-list before -i and never
        # read stdin so a crafted container cannot pull in file:// / http:// sub-inputs.
        "-nostdin",
        *_PROTOCOL_ALLOWLIST_ARGS,
        "-i",
        source_path,
        "-vf",
        select,
        "-vsync",
        "vfr",
        f"{outdir}/f_%03d.png",
    ]
    process = subprocess.Popen(  # nosec B603 B607 - fixed ffmpeg arg list, no shell, sandbox path only
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        _stdout, stderr = process.communicate(timeout=_FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait()
        raise VideoFrameError("frame_extraction_timed_out") from exc
    if process.returncode != 0:
        raise VideoFrameError("unprocessable_video")
    return [float(match) for match in re.findall(r"pts_time:([0-9.]+)", stderr.decode("utf-8", errors="replace"))]


class VideoSceneFrameProcessorAdapter:
    """``MediaProcessorAdapter`` that OCRs a video's scene frames (profile ``video-scene-frames``)."""

    profile_name = "video-scene-frames"

    def __init__(
        self,
        *,
        timeout_seconds: int = _FRAME_TIMEOUT_SECONDS,
        scene_frame_extractor: SceneFrameExtractor | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._scene_frame_extractor = scene_frame_extractor or _default_scene_frame_extractor

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "Video is unprocessable or no frame extractor is available; scene frames cannot be extracted.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "Scene-frame extraction exceeded its time budget; the process is killed and retry is bounded.",
                    timeout_seconds=_FRAME_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == _FRAMES_PROCESSOR

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "scene-frame OCR requires a source_path", request, is_retryable=False)
        frames = self._extract_within_timeout(request)
        units = tuple(
            ProcessedContentUnit(
                unit_kind="video_frame",
                ordinal=index,
                start_ms=start_ms,
                text=text,
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
            for index, (start_ms, text) in enumerate(frames)
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_SCENE_FRAMES_DERIVATIVE,
            content_hash=_content_hash(units),
            mime_type="text/plain",
            units=units,
        )

    def _extract_within_timeout(self, request: MediaProcessingRequest) -> list[tuple[int, str]]:
        assert request.source_path is not None
        # Not a context manager: on timeout we abandon the worker (shutdown wait=False) rather
        # than block on shutdown(wait=True) for a hung ffmpeg/OCR call.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._scene_frame_extractor, request.source_path)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "scene-frame extraction timed out", request, is_retryable=True) from exc
        except VideoFrameError as exc:
            pool.shutdown(wait=False)
            raise self._error("validation", exc.reason, request, is_retryable=False) from exc

    def _error(self, kind: str, reason: str, request: MediaProcessingRequest, *, is_retryable: bool) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"Scene-frame OCR failed: {reason}",
                timeout_seconds=_FRAME_TIMEOUT_SECONDS if kind == "timeout" else None,
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


_SCENE_VISION_DERIVATIVE = "video_scene_vision"
_VISION_PROCESSOR = "video_vision_v1"


def _default_scene_frame_path_extractor(source_path: str, outdir: str) -> list[tuple[int, str]]:
    # No ffmpeg is wired in-process; deterministic unit tests inject a fake. The real
    # ``video-scene-vision`` profile injects the ffmpeg path extractor.
    del source_path, outdir
    raise VideoFrameError("frame_extractor_unavailable")


def _ffmpeg_scene_frame_paths(
    source_path: str, outdir: str, *, scene_sensitivity: str = "MORE_SENSITIVE"
) -> list[tuple[int, str]]:
    # Real engine (L11): extract scene frames with the SAME ffmpeg scene-select as L10 (one PNG
    # per scene frame, ``pts_time`` timecodes from showinfo stderr) into ``outdir``, but return the
    # frame IMAGE paths (not OCR) so a CLIP image embedding can be computed per frame.
    threshold = _scene_threshold(scene_sensitivity)
    timecodes = _run_ffmpeg_scene_select(source_path, threshold, outdir)
    frames = sorted(Path(outdir).glob("f_*.png"))
    return [
        (round((timecodes[index] if index < len(timecodes) else 0.0) * 1000), str(frame))
        for index, frame in enumerate(frames)
    ]


class VideoSceneVisionProcessorAdapter:
    """``MediaProcessorAdapter`` that CLIP-embeds a video's scene frames (profile ``video-scene-vision``).

    The L10 sibling OCRs frames into text; this projects each frame into a CLIP IMAGE embedding so a
    natural-language query ("a photo of a car") can find the frame by visual content — Foundry's
    ``extract_scene_frames`` -> ``imageToEmbeddingsV1`` -> Vector property mode. Each ``video_frame_visual``
    unit carries the CLIP vector + its timecode; the vector (not text) is the searchable content, pinned
    to the vision model version so it only matches inside a vision-model-pinned index generation.
    """

    profile_name = "video-scene-vision"

    def __init__(
        self,
        vision_embedding_model: VisionEmbeddingModelAdapter,
        *,
        timeout_seconds: int = _FRAME_TIMEOUT_SECONDS,
        scene_frame_path_extractor: SceneFramePathExtractor | None = None,
    ) -> None:
        self._vision_embedding_model = vision_embedding_model
        self._timeout_seconds = timeout_seconds
        self._scene_frame_path_extractor = scene_frame_path_extractor or _default_scene_frame_path_extractor

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "Video is unprocessable or no frame/vision engine is available; frames cannot be embedded.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "Scene-frame vision extraction exceeded its budget; the process is killed and retry is bounded.",
                    timeout_seconds=_FRAME_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == _VISION_PROCESSOR

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "scene-frame vision requires a source_path", request, is_retryable=False)
        frames = self._embed_within_timeout(request)
        units = tuple(
            ProcessedContentUnit(
                unit_kind="video_frame_visual",
                ordinal=index,
                start_ms=start_ms,
                text=text,
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                embedding=embedding,
            )
            for index, (start_ms, text, embedding) in enumerate(frames)
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_SCENE_VISION_DERIVATIVE,
            content_hash=_content_hash(units),
            mime_type="application/json",
            units=units,
        )

    def _embed_within_timeout(self, request: MediaProcessingRequest) -> list[tuple[int, str, EmbeddingVector]]:
        assert request.source_path is not None
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._extract_and_embed, request.source_path)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "scene-frame vision timed out", request, is_retryable=True) from exc
        except VideoFrameError as exc:
            pool.shutdown(wait=False)
            raise self._error("validation", exc.reason, request, is_retryable=False) from exc

    def _extract_and_embed(self, source_path: str) -> list[tuple[int, str, EmbeddingVector]]:
        # Extraction + embedding share one tempdir scope so frame files outlive extraction.
        with tempfile.TemporaryDirectory() as outdir:
            frames = self._scene_frame_path_extractor(source_path, outdir)
            if not frames:
                return []
            embeddings = self._vision_embedding_model.embed_images([path for _, path in frames])
            return [
                (start_ms, f"frame@{start_ms}ms", embedding)
                for (start_ms, _path), embedding in zip(frames, embeddings, strict=False)
            ]

    def _error(self, kind: str, reason: str, request: MediaProcessingRequest, *, is_retryable: bool) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"Scene-frame vision failed: {reason}",
                timeout_seconds=_FRAME_TIMEOUT_SECONDS if kind == "timeout" else None,
                details={
                    "mediaItemVersionId": request.media_item_version_id,
                    "processorSpecHash": request.processing_spec_hash,
                    "reason": reason,
                },
            )
        )
