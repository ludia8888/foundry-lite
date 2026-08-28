"""Application service helpers for osdk application artifacts workflows."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import cast

from foundry_lite.application.osdk_manifest import OSDK_PACKAGE_MANIFEST
from foundry_lite.application.ports import (
    OsdkApplicationResourceRow,
    OsdkLanguage,
    OsdkReleaseArtifactDownloadTokenRecord,
    OsdkReleaseArtifactRecord,
    OsdkReleaseArtifactRow,
    OsdkReleaseStatus,
    OsdkSdkCompatibilityWindowRecord,
    OsdkSdkReleaseChannelRecord,
    OsdkSdkVersionBundle,
    OsdkSdkVersionRecord,
    OsdkSdkVersionRow,
    RuntimeJsonObject,
)
from foundry_lite.application.ports.osdk_download_token_signer import (
    OsdkDownloadTokenClaims,
    OsdkDownloadTokenSigner,
)
from foundry_lite.application.ports.osdk_release_artifact_store import OsdkReleaseArtifactStore
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.osdk_application_package_builder import _default_package_name
from foundry_lite.application.services.osdk_application_types import (
    _BUMP_TYPES,
    _DEFAULT_COMPATIBILITY_DAYS,
    _ArtifactDraft,
    _ReleaseDecision,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def artifact_payload(store: OsdkReleaseArtifactStore, row: OsdkReleaseArtifactRow) -> RuntimeJsonObject:
    content = store.read_artifact(row["storage_uri"])
    return {
        "artifact": row,
        "contentBase64": base64.b64encode(content.content).decode("ascii"),
        "contentEncoding": "base64",
    }


def _release_decision(
    latest: OsdkSdkVersionRow | None,
    resources: Sequence[OsdkApplicationResourceRow],
    requested_bump: str | None,
) -> _ReleaseDecision:
    current = _resource_scope_snapshot(resources)
    previous = latest["resource_scope_snapshot"] if latest is not None else None
    required = _required_bump(previous, current)
    requested = requested_bump or required
    if requested not in _BUMP_TYPES:
        raise ValidationFailed("OSDK SDK release bump must be patch, minor, or major")
    status: OsdkReleaseStatus = "released" if _bump_satisfies(required, requested) else "blocked"
    version = _next_version(latest["version"] if latest is not None else None, requested)
    return _ReleaseDecision(version, _compatibility_status(required), status, required)


def _resource_scope_snapshot(resources: Sequence[OsdkApplicationResourceRow]) -> RuntimeJsonObject:
    items = [
        {
            "resourceType": row["resource_type"],
            "resourceApiName": row["resource_api_name"],
            "scopes": sorted(row["scopes"]),
        }
        for row in sorted(resources, key=lambda item: (item["resource_type"], item["resource_api_name"]))
    ]
    return {"resources": items}


def _required_bump(previous: RuntimeJsonObject | None, current: RuntimeJsonObject) -> str:
    if previous is None:
        return "minor"
    previous_scopes = _snapshot_scopes(previous)
    current_scopes = _snapshot_scopes(current)
    if not previous_scopes.issubset(current_scopes):
        return "major"
    if current_scopes != previous_scopes:
        return "minor"
    return "patch"


def _snapshot_scopes(snapshot: RuntimeJsonObject) -> set[tuple[str, str, str]]:
    values: set[tuple[str, str, str]] = set()
    for item in cast(Sequence[Mapping[str, object]], snapshot.get("resources", [])):
        resource_type = str(item.get("resourceType"))
        resource_api_name = str(item.get("resourceApiName"))
        for scope in cast(Sequence[object], item.get("scopes", [])):
            values.add((resource_type, resource_api_name, str(scope)))
    return values


def _release_manifest(
    app: Mapping[str, object],
    resources: Sequence[OsdkApplicationResourceRow],
    decision: _ReleaseDecision,
    language: OsdkLanguage,
    package_name: str | None,
) -> RuntimeJsonObject:
    return {
        "tenantId": app["tenant_id"],
        "appId": app["id"],
        "appApiName": app["app_api_name"],
        "language": language,
        "packageName": package_name or _default_package_name(cast(str, app["app_api_name"]), language),
        "version": decision.version,
        "generatorName": OSDK_PACKAGE_MANIFEST["generator_name"],
        "generatorVersion": OSDK_PACKAGE_MANIFEST["generator_version"],
        "ontologyFingerprint": OSDK_PACKAGE_MANIFEST["ontology_fingerprint"],
        "resourceScopeSnapshot": _resource_scope_snapshot(resources),
        "compatibilityStatus": decision.compatibility_status,
        "releaseStatus": decision.release_status,
    }


def _sdk_record(
    ctx: RequestContext,
    app: Mapping[str, object],
    decision: _ReleaseDecision,
    language: OsdkLanguage,
    artifact: _ArtifactDraft,
    idempotency_key: str,
) -> OsdkSdkVersionRecord:
    now = _now()
    return OsdkSdkVersionRecord(
        sdk_version_id=_new_id("osdk_sdk_version"),
        tenant_id=ctx.tenant_id,
        app_id=cast(str, app["id"]),
        language=language,
        package_name=cast(str, artifact.metadata_json["packageName"]),
        version=decision.version,
        generator_name=cast(str, artifact.metadata_json["generatorName"]),
        generator_version=cast(str, artifact.metadata_json["generatorVersion"]),
        ontology_fingerprint=cast(str, artifact.metadata_json["ontologyFingerprint"]),
        resource_scope_snapshot=cast(RuntimeJsonObject, artifact.metadata_json["resourceScopeSnapshot"]),
        compatibility_status=decision.compatibility_status,
        release_status=decision.release_status,
        artifact_hash=artifact.content_hash if decision.release_status == "released" else None,
        artifact_uri=artifact.storage_uri if decision.release_status == "released" else None,
        created_idempotency_key=idempotency_key,
        created_at=now,
        released_at=now if decision.release_status == "released" else None,
    )


def _artifact_record(
    ctx: RequestContext, app: Mapping[str, object], row: OsdkSdkVersionRow, artifact: _ArtifactDraft
) -> OsdkReleaseArtifactRecord:
    return OsdkReleaseArtifactRecord(
        artifact_id=_new_id("osdk_artifact"),
        tenant_id=ctx.tenant_id,
        app_id=cast(str, app["id"]),
        sdk_version_id=row["id"],
        artifact_kind=artifact.kind,
        content_hash=artifact.content_hash,
        storage_uri=_required_artifact_uri(artifact),
        metadata_json=artifact.metadata_json,
        created_at=_now(),
    )


def _sdk_bundle(row: OsdkSdkVersionRow, artifacts: Sequence[OsdkReleaseArtifactRow]) -> OsdkSdkVersionBundle:
    return {"sdkVersion": row, "artifacts": list(artifacts)}


def _required_artifact_uri(artifact: _ArtifactDraft) -> str:
    if artifact.storage_uri is None:
        raise RuntimeError("released OSDK artifact has no storage URI")
    return artifact.storage_uri


def _release_channel_record(ctx: RequestContext, row: OsdkSdkVersionRow, channel: str) -> OsdkSdkReleaseChannelRecord:
    return OsdkSdkReleaseChannelRecord(
        channel_id=_new_id("osdk_channel"),
        tenant_id=ctx.tenant_id,
        app_id=row["app_id"],
        language=row["language"],
        channel=channel,
        sdk_version_id=row["id"],
        promoted_at=_now(),
        promoted_by_user_id=ctx.actor_user_id,
    )


def _compatibility_window_record(
    ctx: RequestContext,
    old_row: OsdkSdkVersionRow,
    new_row: OsdkSdkVersionRow,
    supported_until: str | None,
) -> OsdkSdkCompatibilityWindowRecord:
    now = _now()
    return OsdkSdkCompatibilityWindowRecord(
        window_id=_new_id("osdk_compat"),
        tenant_id=ctx.tenant_id,
        app_id=old_row["app_id"],
        language=old_row["language"],
        from_sdk_version_id=old_row["id"],
        to_sdk_version_id=new_row["id"],
        status="active",
        supported_until=supported_until or _expires_in_days(now, _DEFAULT_COMPATIBILITY_DAYS),
        created_at=now,
    )


def _download_token_record(
    ctx: RequestContext, artifact: OsdkReleaseArtifactRow, raw_token: str, expires_at: str
) -> OsdkReleaseArtifactDownloadTokenRecord:
    now = _now()
    return OsdkReleaseArtifactDownloadTokenRecord(
        token_id=_new_id("osdk_download"),
        tenant_id=ctx.tenant_id,
        artifact_id=artifact["id"],
        token_hash=_hash_download_token(raw_token),
        status="active",
        expires_at=expires_at,
        created_at=now,
    )


def _request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _with_download_token(
    signer: OsdkDownloadTokenSigner,
    ctx: RequestContext,
    response: Mapping[str, object],
    request_hash: str,
) -> RuntimeJsonObject:
    artifact_id = str(response["artifactId"])
    expires_at = str(response["expiresAt"])
    token = signer.sign_token(OsdkDownloadTokenClaims(ctx.tenant_id, artifact_id, expires_at, request_hash))
    return {"downloadToken": token, "artifactId": artifact_id, "expiresAt": expires_at}


def _hash_download_token(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _expires_in_days(now: str, days: int) -> str:
    return (datetime.fromisoformat(now) + timedelta(days=days)).isoformat()


def _expires_in_seconds(now: str, seconds: int) -> str:
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()


def _is_expired(expires_at: str, now: str) -> bool:
    return datetime.fromisoformat(expires_at) <= datetime.fromisoformat(now)


def _next_version(previous: str | None, bump: str) -> str:
    major, minor, patch = _version_tuple(previous or "0.0.0")
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValidationFailed("stored OSDK SDK version is not semver-compatible")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _bump_satisfies(required: str, requested: str) -> bool:
    order = {"patch": 0, "minor": 1, "major": 2}
    return order[requested] >= order[required]


def _compatibility_status(required_bump: str) -> str:
    if required_bump == "major":
        return "major_version_required"
    if required_bump == "minor":
        return "compatible_additive"
    return "compatible"
