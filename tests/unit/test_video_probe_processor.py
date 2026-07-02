"""Video-probe processor adapter (Media/Content Plane M6).

The probe runner is injected (a deterministic fake), so these run with no ffprobe binary.
Cover the contract: deterministic probe-metadata text + hash, supports() gating, the failure
taxonomy, an unreadable stream as typed validation, a fail-closed timeout, and the default
runner reporting ``probe_engine_unavailable`` (live probe deferred, no system binary).
"""

from __future__ import annotations

import threading

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoProbe,
    VideoProbeError,
    VideoProbeProcessorAdapter,
    _default_probe_runner,
)

_PROBE = VideoProbe(duration_seconds=12.5, container_format="mp4", video_codec="h264", width=1920, height=1080)


def _request(*, processor: str = "video_probe_v1") -> MediaProcessingRequest:
    spec = ProcessorSpec(processor=processor, processor_version="1.0.0")
    return MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="spec-1",
        source_path="/sandbox/clip.mp4",
    )


def test_probe_metadata_is_deterministic() -> None:
    adapter = VideoProbeProcessorAdapter(probe_runner=lambda _path: _PROBE)
    first = adapter.process(_request())
    second = adapter.process(_request())

    assert first.derivative_kind == "video_probe"
    assert first.mime_type == "application/json"
    text = first.units[0].text
    assert "container=mp4" in text and "codec=h264" in text and "width=1920 height=1080" in text
    assert first.content_hash == second.content_hash


def test_supports_only_video_probe_processor() -> None:
    adapter = VideoProbeProcessorAdapter(probe_runner=lambda _path: _PROBE)
    assert adapter.supports(_request()) is True
    assert adapter.supports(_request(processor="ocr_v1")) is False


def test_failure_contract_declares_validation_and_timeout() -> None:
    contract = VideoProbeProcessorAdapter(probe_runner=lambda _path: _PROBE).failure_contract()
    assert contract.adapter_profile == "ffprobe"
    assert {mode.kind for mode in contract.modes} >= {"validation", "timeout"}


def test_unreadable_stream_is_a_validation_failure() -> None:
    def _bad(_path: str) -> VideoProbe:
        raise VideoProbeError("unreadable_stream")

    adapter = VideoProbeProcessorAdapter(probe_runner=_bad)
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request())
    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False


def test_timeout_fails_closed() -> None:
    release = threading.Event()

    def _blocking(_path: str) -> VideoProbe:
        release.wait(timeout=10)
        return _PROBE

    adapter = VideoProbeProcessorAdapter(timeout_seconds=0, probe_runner=_blocking)
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.process(_request())
        assert excinfo.value.failure.kind == "timeout"
        assert excinfo.value.failure.is_retryable is True
    finally:
        release.set()


def test_process_requires_a_sandbox_source_path() -> None:
    adapter = VideoProbeProcessorAdapter(probe_runner=lambda _path: _PROBE)
    request = MediaProcessingRequest(
        tenant_id="t", media_item_version_id="miv-1", blob_key="b", spec=_request().spec, processing_spec_hash="s"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(request)
    assert excinfo.value.failure.kind == "validation"


def test_default_runner_without_a_bundled_ffprobe_is_unavailable() -> None:
    # No ffprobe binary is bundled for CI/local; the default reports a typed reason
    # (a real profile injects a subprocess runner — live video probe is deferred).
    with pytest.raises(VideoProbeError) as excinfo:
        _default_probe_runner("/sandbox/clip.mp4")
    assert excinfo.value.reason == "probe_engine_unavailable"


def test_ffprobe_invocation_restricts_protocols_to_prevent_lfi_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    # A crafted container (concat/playlist referencing a local file URL or a link-local metadata
    # endpoint) must not let ffprobe read local files or reach the network: the arg list must pin
    # an allow-list of protocols and disable stdin.
    from foundry_lite.infrastructure.adapters import video_probe_processor as vpp

    captured: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args: list[str], **_: object) -> None:
            captured.append(args)
            self.returncode = 0

        def communicate(self, timeout: object = None) -> tuple[bytes, bytes]:
            return (b"{}", b"")

    monkeypatch.setattr(vpp.subprocess, "Popen", _FakePopen)
    vpp._run_ffprobe("/sandbox/clip.mp4")

    args = captured[0]
    assert "-protocol_whitelist" in args
    whitelist = args[args.index("-protocol_whitelist") + 1]
    assert "http" not in whitelist and "tcp" not in whitelist
    assert set(whitelist.split(",")) <= {"file", "crypto"}
    assert "-nostdin" in args
    # protocol restriction must precede the input path it guards.
    assert args.index("-protocol_whitelist") < args.index("/sandbox/clip.mp4")


def test_ffmpeg_scene_select_restricts_protocols_to_prevent_lfi_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    from foundry_lite.infrastructure.adapters import video_probe_processor as vpp

    captured: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args: list[str], **_: object) -> None:
            captured.append(args)
            self.returncode = 0

        def communicate(self, timeout: object = None) -> tuple[bytes, bytes]:
            return (b"", b"")

    monkeypatch.setattr(vpp.subprocess, "Popen", _FakePopen)
    vpp._run_ffmpeg_scene_select("/sandbox/clip.mp4", 0.3, "/sandbox/out")

    args = captured[0]
    assert "-protocol_whitelist" in args
    whitelist = args[args.index("-protocol_whitelist") + 1]
    assert "http" not in whitelist and "tcp" not in whitelist
    assert "-nostdin" in args
    # protocol restriction is an input option and must precede -i.
    assert args.index("-protocol_whitelist") < args.index("-i")
