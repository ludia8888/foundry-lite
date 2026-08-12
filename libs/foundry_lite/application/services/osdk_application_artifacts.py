"""Application service helpers for osdk application artifacts workflows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
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
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.osdk_application_types import (
    _BUMP_TYPES,
    _DEFAULT_COMPATIBILITY_DAYS,
    _ArtifactDraft,
    _ReleaseDecision,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


def write_release_artifact(root: Path, manifest: RuntimeJsonObject, language: OsdkLanguage) -> _ArtifactDraft:
    artifact_dir = root / str(manifest["tenantId"]) / str(manifest["appId"]) / str(manifest["version"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = _artifact_path(artifact_dir, manifest, language)
    data = _artifact_bytes(manifest, language)
    path.write_bytes(data)
    return _ArtifactDraft(kind=language, path=path, content_hash=_content_hash(data), metadata_json=manifest)


def artifact_payload(row: OsdkReleaseArtifactRow) -> RuntimeJsonObject:
    path = Path(row["storage_uri"])
    if not path.exists():
        raise NotFound("OSDK release artifact bytes not found", details={"storageUri": row["storage_uri"]})
    return {
        "artifact": row,
        "contentBase64": base64.b64encode(path.read_bytes()).decode("ascii"),
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


def _artifact_bytes(manifest: RuntimeJsonObject, language: OsdkLanguage) -> bytes:
    if language == "typescript":
        return _typescript_package_zip(manifest)
    return _python_package_zip(manifest)


def _typescript_package_zip(manifest: RuntimeJsonObject) -> bytes:
    package_json = {
        "name": manifest["packageName"],
        "version": manifest["version"],
        "private": True,
        "type": "module",
        "exports": {".": "./src/index.ts"},
        "dependencies": {"@foundry-lite/sdk": "^0.1.0"},
    }
    entries = {
        "package.json": json.dumps(package_json, sort_keys=True, indent=2) + "\n",
        "manifest.json": json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        "src/index.ts": _typescript_package_source(manifest),
    }
    return _package_zip(entries)


def _typescript_package_source(manifest: RuntimeJsonObject) -> str:
    snapshot = cast(Mapping[str, object], manifest["resourceScopeSnapshot"])
    resources = cast(Sequence[Mapping[str, object]], snapshot.get("resources", []))
    grouped = {
        kind: [str(item["resourceApiName"]) for item in resources if item.get("resourceType") == kind]
        for kind in ("object", "action", "function")
    }
    lines = [
        "// Generated application-scoped OSDK package. Do not edit by hand.",
        'import type { OsdkActionType, OsdkFunctionType, OsdkObjectType } from "@foundry-lite/sdk";',
        "",
        f"export const SDK_PACKAGE_MANIFEST = {json.dumps(manifest, sort_keys=True)} as const;",
        _typescript_registry("$Objects", grouped["object"], "object", "OsdkObjectType"),
        _typescript_registry("$Actions", grouped["action"], "action", "OsdkActionType"),
        _typescript_registry("$Functions", grouped["function"], "function", "OsdkFunctionType"),
        "",
    ]
    return "\n".join(lines)


def _typescript_registry(name: str, api_names: Sequence[str], kind: str, type_name: str) -> str:
    entries = []
    for api_name in api_names:
        metadata = _typescript_resource_metadata(api_name, kind)
        entries.append(f"  {json.dumps(api_name)}: {metadata} as {type_name},")
    return "\n".join([f"export const {name} = {{", *entries, "} as const;"])


def _typescript_resource_metadata(api_name: str, kind: str) -> str:
    value: dict[str, object]
    if kind == "object":
        value = {
            "kind": kind,
            "apiName": api_name,
            "primaryKey": "objectId",
            "titleProperty": None,
            "properties": [],
            "propertyDatasources": {},
        }
    elif kind == "action":
        value = {"kind": kind, "apiName": api_name, "targetObjectType": "", "targetKind": "object"}
    else:
        value = {"kind": kind, "apiName": api_name, "inputs": [], "output": "unknown"}
    return json.dumps(value, sort_keys=True)


def _python_package_zip(manifest: RuntimeJsonObject) -> bytes:
    package_dir = _python_package_dir(str(manifest["packageName"]))
    module = f"SDK_PACKAGE_MANIFEST = {json.dumps(manifest, sort_keys=True)}\n"
    return _package_zip({f"{package_dir}/__init__.py": module})


def _package_zip(entries: Mapping[str, str]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def _artifact_path(artifact_dir: Path, manifest: RuntimeJsonObject, language: OsdkLanguage) -> Path:
    if language == "typescript":
        return artifact_dir / f"{_artifact_file_stem(str(manifest['packageName']))}-{manifest['version']}.zip"
    return artifact_dir / f"{_python_package_dir(str(manifest['packageName']))}-{manifest['version']}.zip"


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
        artifact_uri=str(artifact.path) if decision.release_status == "released" else None,
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
        storage_uri=str(artifact.path),
        metadata_json=artifact.metadata_json,
        created_at=_now(),
    )


def _sdk_bundle(row: OsdkSdkVersionRow, artifacts: Sequence[OsdkReleaseArtifactRow]) -> OsdkSdkVersionBundle:
    return {"sdkVersion": row, "artifacts": list(artifacts)}


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


def _default_package_name(app_api_name: str, language: OsdkLanguage) -> str:
    suffix = app_api_name.replace("_", "-").lower()
    return f"@foundry-lite/{suffix}-osdk" if language == "typescript" else f"foundry_lite_{suffix}_osdk"


def _python_package_dir(package_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", package_name).strip("_") or "foundry_lite_osdk"


def _artifact_file_stem(package_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", package_name).strip("._-") or "foundry-lite-osdk"


def _content_hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _with_download_token(
    root: Path, ctx: RequestContext, response: Mapping[str, object], request_hash: str
) -> RuntimeJsonObject:
    artifact_id = str(response["artifactId"])
    expires_at = str(response["expiresAt"])
    token = _signed_download_token(root, ctx.tenant_id, artifact_id, expires_at, request_hash)
    return {"downloadToken": token, "artifactId": artifact_id, "expiresAt": expires_at}


def _signed_download_token(root: Path, tenant_id: str, artifact_id: str, expires_at: str, request_hash: str) -> str:
    payload = {"tenantId": tenant_id, "artifactId": artifact_id, "expiresAt": expires_at, "requestHash": request_hash}
    body = _base64_url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _base64_url(hmac.new(_download_token_secret(root), body.encode("ascii"), hashlib.sha256).digest())
    return f"osdkdl.{body}.{signature}"


def _download_token_secret(root: Path) -> bytes:
    path = root / "osdk-download-token-secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return base64.urlsafe_b64decode(path.read_bytes())
    secret = secrets.token_bytes(32)
    path.write_bytes(base64.urlsafe_b64encode(secret))
    return secret


def _base64_url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


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
