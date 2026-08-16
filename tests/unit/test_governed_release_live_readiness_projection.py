from __future__ import annotations

import json
from pathlib import Path

import pytest
from foundry_lite_api.governed_release_live_readiness import (
    _MAX_ARTIFACT_BYTES,
    MANIFEST_PATH_ENV,
    PREFLIGHT_PATH_ENV,
    VERIFICATION_PATH_ENV,
    _path,
    _read_object,
    live_readiness_payload,
)


def test_readiness_uses_foundry_home_and_blocks_when_operator_artifacts_are_absent(
    tmp_path: Path,
) -> None:
    payload = live_readiness_payload("release-app", {"FOUNDRY_LITE_HOME": str(tmp_path)})

    assert payload["status"] == "blocked"
    assert payload["is_ready_for_live_run"] is False
    assert payload["blockers"] == (
        "golden_manifest_missing",
        "live_preflight_missing",
        "golden_write_evidence_missing",
    )


def test_readiness_reads_only_explicit_bounded_json_objects(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    preflight_path = tmp_path / "preflight.json"
    verification_path = tmp_path / "verification.json"
    manifest_path.write_text(json.dumps({"applicationId": "release-app"}), encoding="utf-8")
    preflight_path.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
    verification_path.write_text(json.dumps({"status": "live_verified"}), encoding="utf-8")
    environ = {
        MANIFEST_PATH_ENV: f"  {manifest_path}  ",
        PREFLIGHT_PATH_ENV: str(preflight_path),
        VERIFICATION_PATH_ENV: str(verification_path),
    }

    payload = live_readiness_payload("release-app", environ)

    assert payload["status"] == "blocked"
    assert payload["manifest_digest"].startswith("sha256:")
    assert _path(environ, MANIFEST_PATH_ENV, Path("fallback.json")) == manifest_path


@pytest.mark.parametrize(
    "contents",
    [b"", b"{", b"[]", b"\xff\xfe", b"x" * (_MAX_ARTIFACT_BYTES + 1)],
    ids=["empty", "malformed-json", "non-object", "invalid-utf8", "oversized"],
)
def test_operator_artifact_reader_fails_closed_for_untrusted_file_shapes(
    tmp_path: Path,
    contents: bytes,
) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(contents)

    assert _read_object(path) is None


def test_operator_artifact_reader_rejects_missing_paths_and_accepts_objects(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    valid = tmp_path / "valid.json"
    valid.write_text('{"status":"ready"}', encoding="utf-8")

    assert _read_object(missing) is None
    assert _read_object(valid) == {"status": "ready"}
