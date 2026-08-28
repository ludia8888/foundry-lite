"""Deterministic TypeScript and Python package generation for OSDK releases."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import OsdkLanguage, RuntimeJsonObject
from foundry_lite.application.ports.osdk_release_artifact_store import (
    OsdkReleaseArtifactStore,
    OsdkReleaseArtifactWrite,
)
from foundry_lite.application.services.osdk_application_types import _ArtifactDraft


def build_release_artifact(
    store: OsdkReleaseArtifactStore,
    manifest: RuntimeJsonObject,
    language: OsdkLanguage,
    *,
    should_persist: bool,
) -> _ArtifactDraft:
    data = _artifact_bytes(manifest, language)
    stored = store.write_artifact(_artifact_write(manifest, language, data)) if should_persist else None
    return _ArtifactDraft(
        kind=language,
        storage_uri=stored.storage_uri if stored is not None else None,
        content_hash=stored.content_hash if stored is not None else _content_hash(data),
        metadata_json=manifest,
    )


def _artifact_write(
    manifest: RuntimeJsonObject,
    language: OsdkLanguage,
    data: bytes,
) -> OsdkReleaseArtifactWrite:
    return OsdkReleaseArtifactWrite(
        tenant_id=str(manifest["tenantId"]),
        app_id=str(manifest["appId"]),
        version=str(manifest["version"]),
        file_name=_artifact_file_name(manifest, language),
        content=data,
    )


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
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def _artifact_file_name(manifest: RuntimeJsonObject, language: OsdkLanguage) -> str:
    if language == "typescript":
        return f"{_artifact_file_stem(str(manifest['packageName']))}-{manifest['version']}.zip"
    return f"{_python_package_dir(str(manifest['packageName']))}-{manifest['version']}.zip"


def _default_package_name(app_api_name: str, language: OsdkLanguage) -> str:
    suffix = app_api_name.replace("_", "-").lower()
    return f"@foundry-lite/{suffix}-osdk" if language == "typescript" else f"foundry_lite_{suffix}_osdk"


def _python_package_dir(package_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", package_name).strip("_") or "foundry_lite_osdk"


def _artifact_file_stem(package_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", package_name).strip("._-") or "foundry-lite-osdk"


def _content_hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"
