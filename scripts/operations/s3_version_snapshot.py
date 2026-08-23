"""Stream a hash-verified, version-aware S3 snapshot to or from a tar archive."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import BinaryIO, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

_MAX_OBJECTS = 250_000
_MAX_TOTAL_BYTES = 50 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class VersionEntry:
    key: str
    version_id: str
    is_delete_marker: bool
    size: int
    last_modified: str
    sha256: str | None


class _ReadableBody(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _ClosableBody(_ReadableBody, Protocol):
    def close(self) -> None: ...


class S3SnapshotClient(Protocol):
    def list_object_versions(self, **arguments: object) -> Mapping[str, object]: ...

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, object]: ...

    def put_object(self, *, Bucket: str, Key: str, Body: object, ContentLength: int) -> object: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...

    def delete_objects(self, *, Bucket: str, Delete: Mapping[str, object]) -> Mapping[str, object]: ...


class HashingReader:
    def __init__(self, source: _ReadableBody) -> None:
        self._source = source
        self._digest = hashlib.sha256()

    def read(self, size: int = -1, /) -> bytes:
        value = self._source.read(size)
        self._digest.update(value)
        return value

    @property
    def digest(self) -> str:
        return "sha256:" + self._digest.hexdigest()


def build_manifest(client: S3SnapshotClient, bucket: str) -> tuple[VersionEntry, ...]:
    return tuple(_entry_with_hash(client, bucket, entry) for entry in _listed_manifest(client, bucket))


def _listed_manifest(client: S3SnapshotClient, bucket: str) -> tuple[VersionEntry, ...]:
    raw = list(_list_versions(client, bucket))
    if len(raw) > _MAX_OBJECTS:
        raise RuntimeError("s3_snapshot_object_limit_exceeded")
    ordered = sorted(raw, key=lambda item: (item["Key"], item["LastModified"], item["VersionId"]))
    entries: list[VersionEntry] = []
    total_bytes = 0
    for item in ordered:
        size = _listed_size(item)
        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES:
            raise RuntimeError("s3_snapshot_size_limit_exceeded")
        is_delete_marker = bool(item["IsDeleteMarker"])
        key = _listed_text(item, "Key")
        version_id = _listed_text(item, "VersionId")
        entries.append(
            VersionEntry(
                key=key,
                version_id=version_id,
                is_delete_marker=is_delete_marker,
                size=size,
                last_modified=_listed_datetime(item).isoformat(),
                sha256=None,
            )
        )
    return tuple(entries)


def _entry_with_hash(client: S3SnapshotClient, bucket: str, entry: VersionEntry) -> VersionEntry:
    if entry.is_delete_marker:
        return entry
    return replace(entry, sha256=_object_hash(client, bucket, entry.key, entry.version_id))


def export_archive(client: S3SnapshotClient, bucket: str, output: BinaryIO) -> tuple[VersionEntry, ...]:
    listed = _listed_manifest(client, bucket)
    completed: list[VersionEntry] = []
    with tarfile.open(fileobj=output, mode="w|") as archive:
        for index, entry in enumerate(listed):
            completed.append(_add_archive_entry(archive, client, bucket, index, entry))
        manifest = tuple(completed)
        _add_manifest(archive, bucket, manifest)
    return manifest


def _add_archive_entry(
    archive: tarfile.TarFile,
    client: S3SnapshotClient,
    bucket: str,
    index: int,
    entry: VersionEntry,
) -> VersionEntry:
    info = tarfile.TarInfo(f"markers/{index:08d}.delete" if entry.is_delete_marker else f"objects/{index:08d}.bin")
    info.size = entry.size
    info.mode = 0o600
    info.pax_headers = {"foundry.key": entry.key, "foundry.version": entry.version_id}
    if entry.is_delete_marker:
        info.pax_headers["foundry.delete"] = "true"
        archive.addfile(info, io.BytesIO())
        return entry
    body = _body(client.get_object(Bucket=bucket, Key=entry.key, VersionId=entry.version_id))
    reader = HashingReader(body)
    try:
        archive.addfile(info, reader)
    finally:
        body.close()
    return replace(entry, sha256=reader.digest)


def import_archive(client: S3SnapshotClient, bucket: str, source: BinaryIO) -> tuple[VersionEntry, ...]:
    if any(_list_versions(client, bucket)):
        raise RuntimeError("s3_snapshot_restore_bucket_not_empty")
    expected: tuple[VersionEntry, ...] | None = None
    restored: set[tuple[str, str]] = set()
    with tarfile.open(fileobj=source, mode="r|") as archive:
        for item in archive:
            member = archive.extractfile(item)
            if member is None:
                continue
            if item.name == "manifest.json":
                expected = _manifest_from_payload(json.load(member), bucket)
                continue
            restored.add(_restore_archive_entry(client, bucket, item, member))
    return _validate_restored_archive(client, bucket, expected, restored)


def purge_bucket(client: S3SnapshotClient, bucket: str) -> int:
    versions = list(_list_versions(client, bucket))
    if len(versions) > _MAX_OBJECTS:
        raise RuntimeError("s3_snapshot_object_limit_exceeded")
    for start in range(0, len(versions), 1000):
        objects = [{"Key": item["Key"], "VersionId": item["VersionId"]} for item in versions[start : start + 1000]]
        response = client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        if _listed_items(response.get("Errors")):
            raise RuntimeError("s3_snapshot_purge_failed")
    if any(_list_versions(client, bucket)):
        raise RuntimeError("s3_snapshot_purge_incomplete")
    return len(versions)


def _restore_archive_entry(
    client: S3SnapshotClient,
    bucket: str,
    item: tarfile.TarInfo,
    member: _ReadableBody,
) -> tuple[str, str]:
    key, version, is_delete_marker, digest = _archive_entry_metadata(item)
    if is_delete_marker:
        client.delete_object(Bucket=bucket, Key=key)
        return key, version
    reader = HashingReader(member)
    client.put_object(Bucket=bucket, Key=key, Body=reader, ContentLength=item.size)
    if digest is not None and reader.digest != digest:
        raise RuntimeError("s3_snapshot_content_hash_mismatch")
    return key, version


def _archive_entry_metadata(item: tarfile.TarInfo) -> tuple[str, str, bool, str | None]:
    key = item.pax_headers.get("foundry.key", "")
    version = item.pax_headers.get("foundry.version", "")
    is_delete_marker = item.pax_headers.get("foundry.delete") == "true"
    digest = item.pax_headers.get("foundry.sha256")
    if not key or not version:
        raise RuntimeError("s3_snapshot_archive_metadata_invalid")
    return key, version, is_delete_marker, digest


def _validate_restored_archive(
    client: S3SnapshotClient,
    bucket: str,
    expected: tuple[VersionEntry, ...] | None,
    restored: set[tuple[str, str]],
) -> tuple[VersionEntry, ...]:
    if expected is None:
        raise RuntimeError("s3_snapshot_manifest_missing")
    required = {(entry.key, entry.version_id) for entry in expected}
    if restored != required:
        raise RuntimeError("s3_snapshot_object_set_mismatch")
    observed = build_manifest(client, bucket)
    if _content_coordinates(observed) != _content_coordinates(expected):
        raise RuntimeError("s3_snapshot_restored_manifest_mismatch")
    return expected


def _list_versions(client: S3SnapshotClient, bucket: str) -> Iterator[dict[str, object]]:
    key_marker: str | None = None
    version_marker: str | None = None
    while True:
        arguments: dict[str, object] = {"Bucket": bucket, "MaxKeys": 1000}
        if key_marker:
            arguments["KeyMarker"] = key_marker
        if version_marker:
            arguments["VersionIdMarker"] = version_marker
        page = client.list_object_versions(**arguments)
        for item in _listed_items(page.get("Versions")):
            yield {**item, "IsDeleteMarker": False}
        for item in _listed_items(page.get("DeleteMarkers")):
            yield {**item, "IsDeleteMarker": True, "Size": 0}
        if not page.get("IsTruncated"):
            return
        next_key_marker = page.get("NextKeyMarker")
        next_version_marker = page.get("NextVersionIdMarker")
        if not isinstance(next_key_marker, str):
            raise RuntimeError("s3_snapshot_pagination_invalid")
        if next_version_marker is not None and not isinstance(next_version_marker, str):
            raise RuntimeError("s3_snapshot_pagination_invalid")
        key_marker = next_key_marker
        version_marker = next_version_marker


def _object_hash(client: S3SnapshotClient, bucket: str, key: str, version_id: str) -> str:
    response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
    body = _body(response)
    digest = hashlib.sha256()
    try:
        while chunk := body.read(1024 * 1024):
            digest.update(chunk)
    finally:
        body.close()
    return "sha256:" + digest.hexdigest()


def _add_manifest(archive: tarfile.TarFile, bucket: str, manifest: tuple[VersionEntry, ...]) -> None:
    payload = _manifest_payload(bucket, manifest)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    info = tarfile.TarInfo("manifest.json")
    info.size = len(encoded)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(encoded))


def _manifest_payload(bucket: str, manifest: tuple[VersionEntry, ...]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "bucket": bucket,
        "entries": [
            {
                "key": entry.key,
                "versionId": entry.version_id,
                "isDeleteMarker": entry.is_delete_marker,
                "size": entry.size,
                "lastModified": entry.last_modified,
                "sha256": entry.sha256,
            }
            for entry in manifest
        ],
    }


def _manifest_from_payload(payload: object, bucket: str) -> tuple[VersionEntry, ...]:
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != 1 or payload.get("bucket") != bucket:
        raise RuntimeError("s3_snapshot_manifest_invalid")
    values = payload.get("entries")
    if not isinstance(values, list):
        raise RuntimeError("s3_snapshot_manifest_invalid")
    entries: list[VersionEntry] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise RuntimeError("s3_snapshot_manifest_invalid")
        entries.append(
            VersionEntry(
                key=_text(value, "key"),
                version_id=_text(value, "versionId"),
                is_delete_marker=_boolean(value, "isDeleteMarker"),
                size=_size(value),
                last_modified=_text(value, "lastModified"),
                sha256=_optional_text(value.get("sha256")),
            )
        )
    return tuple(entries)


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RuntimeError("s3_snapshot_manifest_invalid")
    return item


def _boolean(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise RuntimeError("s3_snapshot_manifest_invalid")
    return item


def _size(value: Mapping[str, object]) -> int:
    item = value.get("size")
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise RuntimeError("s3_snapshot_manifest_invalid")
    return item


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RuntimeError("s3_snapshot_manifest_invalid")
    return value


def _content_coordinates(entries: tuple[VersionEntry, ...]) -> tuple[tuple[str, bool, int, str | None], ...]:
    return tuple((entry.key, entry.is_delete_marker, entry.size, entry.sha256) for entry in entries)


def _listed_items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _listed_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RuntimeError("s3_snapshot_listing_invalid")
    return item


def _listed_size(value: Mapping[str, object]) -> int:
    item = value.get("Size", 0)
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise RuntimeError("s3_snapshot_listing_invalid")
    return item


def _listed_datetime(value: Mapping[str, object]) -> datetime:
    item = value.get("LastModified")
    if not isinstance(item, datetime) or item.tzinfo is None:
        raise RuntimeError("s3_snapshot_listing_invalid")
    return item


def _body(response: Mapping[str, object]) -> _ClosableBody:
    body = response.get("Body")
    if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
        raise RuntimeError("s3_snapshot_body_invalid")
    return cast(_ClosableBody, body)


def _client() -> tuple[S3SnapshotClient, str]:
    endpoint = os.environ.get("FOUNDRY_LITE_S3_ENDPOINT_URL", "")
    bucket = os.environ.get("FOUNDRY_LITE_S3_BUCKET", "")
    if not endpoint or not bucket:
        raise RuntimeError("s3_snapshot_configuration_missing")
    config = Config(request_checksum_calculation="when_required", s3={"payload_signing_enabled": False})
    return cast(S3SnapshotClient, boto3.client("s3", endpoint_url=endpoint, config=config)), bucket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("manifest", "export", "import", "purge"))
    args = parser.parse_args()
    client, bucket = _client()
    if args.mode == "manifest":
        payload = _manifest_payload(bucket, build_manifest(client, bucket))
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    elif args.mode == "export":
        export_archive(client, bucket, sys.stdout.buffer)
    elif args.mode == "import":
        import_archive(client, bucket, sys.stdin.buffer)
    else:
        deleted = purge_bucket(client, bucket)
        print(json.dumps({"deletedVersionCount": deleted, "status": "passed"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
