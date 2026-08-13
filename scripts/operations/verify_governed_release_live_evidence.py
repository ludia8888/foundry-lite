"""Verify hosted Governed Release golden evidence without performing mutations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO, cast

from foundry_lite.application.services.aip.governed_release_live_evidence import (
    VERIFICATION_SCHEMA,
    serialize_verification,
    verify_golden_evidence,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "artifacts" / "operations" / "governed_release_golden_manifest.json"
DEFAULT_EVIDENCE = ROOT / "artifacts" / "operations" / "governed_release_golden_evidence.json"
DEFAULT_PREFLIGHT = ROOT / "artifacts" / "operations" / "governed_release_live_preflight.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "operations" / "governed_release_golden_verification.json"
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MANIFEST_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_MANIFEST_PATH"
PREFLIGHT_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_LIVE_PREFLIGHT_PATH"
VERIFICATION_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_VERIFICATION_PATH"
EVIDENCE_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_EVIDENCE_PATH"


class EvidenceFileInvalid(ValueError):
    pass


def verify_files(manifest_path: Path, evidence_path: Path, preflight_path: Path) -> str:
    """Return only normalized verification output; source artifacts remain untouched."""

    manifest = _read_object(manifest_path)
    evidence = _read_object(evidence_path)
    preflight = _read_object(preflight_path)
    return serialize_verification(verify_golden_evidence(manifest, evidence, preflight))


def _read_object(path: Path) -> Mapping[str, object]:
    try:
        size = path.stat().st_size
    except OSError:
        raise EvidenceFileInvalid("required_evidence_file_missing") from None
    if size < 2 or size > _MAX_ARTIFACT_BYTES:
        raise EvidenceFileInvalid("evidence_file_size_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFileInvalid("evidence_file_json_invalid") from None
    if not isinstance(payload, Mapping):
        raise EvidenceFileInvalid("evidence_file_object_required")
    return cast(Mapping[str, object], payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify hosted Governed Release golden-run evidence.")
    parser.add_argument("--manifest", type=Path, default=_environment_path(MANIFEST_PATH_ENV, DEFAULT_MANIFEST))
    parser.add_argument("--evidence", type=Path, default=_environment_path(EVIDENCE_PATH_ENV, DEFAULT_EVIDENCE))
    parser.add_argument("--preflight", type=Path, default=_environment_path(PREFLIGHT_PATH_ENV, DEFAULT_PREFLIGHT))
    parser.add_argument("--output", type=Path, default=_environment_path(VERIFICATION_PATH_ENV, DEFAULT_OUTPUT))
    return parser


def _environment_path(name: str, fallback: Path) -> Path:
    configured = os.environ.get(name, "").strip()
    if configured:
        return Path(configured)
    foundry_home = os.environ.get("FOUNDRY_LITE_HOME", "").strip()
    return Path(foundry_home) / "operator-evidence" / fallback.name if foundry_home else fallback


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        serialized = verify_files(args.manifest, args.evidence, args.preflight)
        payload = json.loads(serialized)
    except EvidenceFileInvalid as exc:
        payload = {
            "schema_version": VERIFICATION_SCHEMA,
            "status": "blocked",
            "is_structurally_complete": False,
            "is_live_verified": False,
            "blockers": [str(exc)],
        }
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    (stdout or sys.stdout).write(serialized)
    return 0 if payload.get("is_live_verified") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
