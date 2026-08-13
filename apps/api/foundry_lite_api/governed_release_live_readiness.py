"""Safe artifact-backed readiness projection for the hosted release plane."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from foundry_lite.application.services.aip.governed_release_live_evidence import assess_live_readiness

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_MANIFEST_PATH"
PREFLIGHT_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_LIVE_PREFLIGHT_PATH"
VERIFICATION_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_VERIFICATION_PATH"
DEFAULT_MANIFEST_PATH = ROOT / "artifacts" / "operations" / "governed_release_golden_manifest.json"
DEFAULT_PREFLIGHT_PATH = ROOT / "artifacts" / "operations" / "governed_release_live_preflight.json"
DEFAULT_VERIFICATION_PATH = ROOT / "artifacts" / "operations" / "governed_release_golden_verification.json"
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


def live_readiness_payload(
    application_id: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a redacted result; absent or malformed artifacts are simply blockers."""

    source = os.environ if environ is None else environ
    manifest = _read_object(_path(source, MANIFEST_PATH_ENV, DEFAULT_MANIFEST_PATH))
    preflight = _read_object(_path(source, PREFLIGHT_PATH_ENV, DEFAULT_PREFLIGHT_PATH))
    verification = _read_object(_path(source, VERIFICATION_PATH_ENV, DEFAULT_VERIFICATION_PATH))
    return asdict(assess_live_readiness(application_id, manifest, preflight, verification))


def _path(source: Mapping[str, str], name: str, default: Path) -> Path:
    value = source.get(name, "").strip()
    if value:
        return Path(value)
    foundry_home = source.get("FOUNDRY_LITE_HOME", "").strip()
    return Path(foundry_home) / "operator-evidence" / default.name if foundry_home else default


def _read_object(path: Path) -> Mapping[str, object] | None:
    try:
        if not path.is_file() or not 2 <= path.stat().st_size <= _MAX_ARTIFACT_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


__all__ = [
    "MANIFEST_PATH_ENV",
    "PREFLIGHT_PATH_ENV",
    "VERIFICATION_PATH_ENV",
    "live_readiness_payload",
]
