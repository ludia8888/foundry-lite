"""Pure backup artifact restore rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.errors import ConflictDetected


def artifact_restore_findings(
    artifact_preflight: Mapping[str, object],
    current_preflight: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return blocking findings when an artifact no longer matches serving state."""
    findings: list[dict[str, object]] = []
    if artifact_preflight.get("status") != "ready":
        findings.append({"check": "artifact_preflight", "status": artifact_preflight.get("status")})
    if current_preflight.get("status") != "ready":
        findings.append({"check": "current_preflight", "status": current_preflight.get("status")})
    findings.extend(_artifact_version_findings(artifact_preflight, current_preflight))
    return findings


def latest_versions_by_dataset(
    versions: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str], Mapping[str, object]]:
    """Return the highest version number for each dataset/branch pair."""
    latest: dict[tuple[str, str], Mapping[str, object]] = {}
    for version in versions:
        key = (str(version["datasetId"]), str(version["branch"]))
        current = latest.get(key)
        if current is None or _version_number(version) > _version_number(current):
            latest[key] = version
    return latest


def require_artifact_tenant(request_tenant_id: str, artifact_tenant_id: str) -> None:
    """Fail closed when a backup artifact belongs to another tenant."""
    if artifact_tenant_id == request_tenant_id:
        return
    raise ConflictDetected(
        "backup artifact tenant does not match request context",
        details={"artifact_tenant_id": artifact_tenant_id, "request_tenant_id": request_tenant_id},
    )


def _artifact_version_findings(
    artifact_preflight: Mapping[str, object],
    current_preflight: Mapping[str, object],
) -> list[dict[str, object]]:
    artifact_versions = _versions(artifact_preflight)
    current_versions = _versions(current_preflight)
    current_by_version = {str(version["versionId"]): version for version in current_versions}
    latest_by_dataset = latest_versions_by_dataset(current_versions)
    findings: list[dict[str, object]] = []
    for artifact_version in artifact_versions:
        current_version = current_by_version.get(str(artifact_version["versionId"]))
        findings.extend(_version_mismatch_findings(artifact_version, current_version))
        latest = latest_by_dataset.get((str(artifact_version["datasetId"]), str(artifact_version["branch"])))
        if latest is not None and latest["versionId"] != artifact_version["versionId"]:
            findings.append(_serving_head_finding(artifact_version, latest))
    return findings


def _versions(preflight: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = preflight.get("datasetVersions")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    items = cast(Sequence[object], value)
    return [cast(Mapping[str, object], item) for item in items if isinstance(item, Mapping)]


def _version_mismatch_findings(
    artifact_version: Mapping[str, object],
    current_version: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    if current_version is None:
        return [
            {
                "check": "artifact_dataset_version_present",
                "status": "missing",
                "versionId": artifact_version["versionId"],
            }
        ]
    mismatches = _mismatched_version_fields(artifact_version, current_version)
    if not mismatches:
        return []
    return [{"check": "artifact_dataset_version_integrity", "status": "mismatch", "fields": mismatches}]


def _mismatched_version_fields(
    artifact_version: Mapping[str, object],
    current_version: Mapping[str, object],
) -> list[str]:
    fields = ("datasetId", "manifestUri", "rowCount", "byteSize", "manifestContentHashes", "status")
    return [field for field in fields if artifact_version.get(field) != current_version.get(field)]


def _version_number(version: Mapping[str, object]) -> int:
    value = version["versionNumber"]
    return int(value) if isinstance(value, int | str) else 0


def _serving_head_finding(
    artifact_version: Mapping[str, object],
    latest: Mapping[str, object],
) -> dict[str, object]:
    return {
        "check": "serving_head_matches_artifact",
        "status": "blocked",
        "datasetId": artifact_version["datasetId"],
        "artifactVersionId": artifact_version["versionId"],
        "currentHeadVersionId": latest["versionId"],
    }
