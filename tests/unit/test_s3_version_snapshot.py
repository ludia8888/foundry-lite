from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import pytest

from scripts.operations import s3_version_snapshot as subject
from scripts.operations.s3_version_snapshot import build_manifest, export_archive, import_archive


class _ConfigView(Protocol):
    request_checksum_calculation: str
    s3: dict[str, object]


class _FakeS3:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.get_calls = 0
        self._sequence = 0

    def add(self, key: str, body: bytes) -> None:
        self._sequence += 1
        self.events.append(self._event(key, body, False))

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        assert Bucket == "qa-bucket"
        self._sequence += 1
        self.events.append(self._event(Key, b"", True))

    def list_object_versions(self, **arguments: object) -> dict[str, object]:
        assert arguments["Bucket"] == "qa-bucket"
        versions = [self._listed(item) for item in self.events if not item["delete"]]
        markers = [self._listed(item) for item in self.events if item["delete"]]
        return {"Versions": versions, "DeleteMarkers": markers, "IsTruncated": False}

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        assert Bucket == "qa-bucket"
        self.get_calls += 1
        item = next(value for value in self.events if value["key"] == Key and value["version"] == VersionId)
        return {"Body": io.BytesIO(item["body"])}

    def put_object(self, *, Bucket: str, Key: str, Body: object, ContentLength: int) -> None:
        assert Bucket == "qa-bucket"
        body = Body.read(ContentLength)
        assert len(body) == ContentLength
        self.add(Key, body)

    def _event(self, key: str, body: bytes, is_delete: bool) -> dict[str, object]:
        return {
            "key": key,
            "body": body,
            "delete": is_delete,
            "version": f"version-{self._sequence}",
            "at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self._sequence),
        }

    @staticmethod
    def _listed(item: dict[str, object]) -> dict[str, object]:
        return {
            "Key": item["key"],
            "VersionId": item["version"],
            "LastModified": item["at"],
            "Size": len(item["body"]),
        }


class _CorruptingS3(_FakeS3):
    def put_object(self, *, Bucket: str, Key: str, Body: object, ContentLength: int) -> None:
        assert Bucket == "qa-bucket"
        body = Body.read(ContentLength)
        self.add(Key, body + b"corrupt")


def test_s3_version_snapshot_round_trips_version_content_and_delete_order() -> None:
    source = _FakeS3()
    source.add("receipts/a.txt", b"first")
    source.delete_object(Bucket="qa-bucket", Key="receipts/a.txt")
    source.add("receipts/a.txt", b"second")
    archive = io.BytesIO()

    expected = export_archive(source, "qa-bucket", archive)
    assert source.get_calls == 2
    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:") as exported:
        members = exported.getmembers()
    assert members[-1].name == "manifest.json"
    assert all("foundry.sha256" not in member.pax_headers for member in members if member.name.startswith("objects/"))
    target = _FakeS3()
    archive.seek(0)
    restored = import_archive(target, "qa-bucket", archive)

    assert restored == expected
    assert [entry.sha256 for entry in build_manifest(target, "qa-bucket")] == [entry.sha256 for entry in expected]
    assert target.events[-1]["body"] == b"second"


def test_s3_snapshot_manifest_detects_corruption_after_streamed_restore() -> None:
    source = _FakeS3()
    source.add("receipts/a.txt", b"source-bytes")
    archive = io.BytesIO()
    export_archive(source, "qa-bucket", archive)
    archive.seek(0)

    with pytest.raises(RuntimeError, match="restored_manifest_mismatch"):
        import_archive(_CorruptingS3(), "qa-bucket", archive)


def test_s3_version_snapshot_refuses_nonempty_restore_target() -> None:
    target = _FakeS3()
    target.add("existing", b"data")
    with pytest.raises(RuntimeError, match="restore_bucket_not_empty"):
        import_archive(target, "qa-bucket", io.BytesIO())


def test_s3_snapshot_client_streams_without_rewinding_tar_members(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def client(service: str, **kwargs: object) -> object:
        captured.update({"service": service, **kwargs})
        return object()

    monkeypatch.setenv("FOUNDRY_LITE_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("FOUNDRY_LITE_S3_BUCKET", "qa-bucket")
    monkeypatch.setattr(subject.boto3, "client", client)

    _, bucket = subject._client()

    config = cast(_ConfigView, captured["config"])
    assert bucket == "qa-bucket"
    assert captured["service"] == "s3"
    assert config.request_checksum_calculation == "when_required"
    assert config.s3["payload_signing_enabled"] is False


def test_s3_snapshot_object_bound_has_repeated_soak_headroom() -> None:
    assert subject._MAX_OBJECTS == 250_000
