"""S3 MediaStorageAdapter behaviour against live MinIO (media plane M1, ADR-0001 §5.1).

A blob lives at one immutable key; commit is a DB metadata-pointer (no S3 copy). These
cases pin the upload→verify→read→grant lifecycle plus the failure modes M1 must survive:
corruption detection (§6.2), typed retryable upload failure, interrupted-multipart
cleanup, concurrent same-path uploads, retry idempotency, and tenant/version-scoped
expiring read grants (M-T0-010).
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import import_module
from time import monotonic, sleep
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_storage import (
    ByteRange,
    CompleteMediaUpload,
    InitiateMediaUpload,
    MediaReadGrantRequest,
)
from foundry_lite.infrastructure.adapters import S3MediaStorageAdapter, S3MediaStorageConfig
from testcontainers.core.container import DockerContainer

MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


class _WriteTimeoutClient:
    """Wraps a real client; put_object raises a timeout to exercise failure mapping."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def put_object(self, **kwargs: Any) -> object:
        del kwargs
        raise TimeoutError("S3 media upload timed out")


class _FakeClientError(Exception):
    """botocore.ClientError-shaped error carrying an S3 error code and HTTP status."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class _AccessForbiddenGetClient:
    """Reads fail with a forbidden (403) error, not a missing-object 404."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def get_object(self, **kwargs: Any) -> object:
        del kwargs
        raise _FakeClientError("AccessDenied", 403)


class _GrantFailClient:
    """Presigned-URL generation fails, exercising the read-grant failure mapping."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def generate_presigned_url(self, *args: Any, **kwargs: Any) -> object:
        del args, kwargs
        raise RuntimeError("presigned url generation unavailable")


@pytest.fixture(scope="session")
def minio_server() -> Iterator[MinioServer]:
    container = (
        DockerContainer("minio/minio")
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000)
    )
    container.start()
    try:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        _wait_until(lambda: _minio_ready(endpoint))
        yield MinioServer(endpoint_url=endpoint)
    finally:
        container.stop()


def _initiate(adapter: S3MediaStorageAdapter, *, logical_path: str = "/a.pdf", declared: int | None = None) -> str:
    session = adapter.initiate_upload(
        InitiateMediaUpload(
            tenant_id="tenant-demo",
            media_set_id="ms-1",
            logical_path=logical_path,
            supplied_mime_type="application/pdf",
            declared_byte_size=declared,
        )
    )
    return session.staged_object_key


def _stage(adapter: S3MediaStorageAdapter, body: bytes, *, logical_path: str = "/a.pdf") -> str:
    key = _initiate(adapter, logical_path=logical_path)
    adapter.write_staged(key, io.BytesIO(body))
    return key


def test_s3_media_adapter_contract_declares_failure_modes(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    contract = adapter.failure_contract()
    assert adapter.profile_name == "s3-media"
    assert contract.adapter_profile == "s3-media"
    operations = {mode.operation for mode in contract.modes}
    assert {"write_staged", "stat", "delete_uncommitted"} <= operations


def test_s3_media_upload_stat_stream_and_grant_round_trip(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    body = b"%PDF-1.4 hello world"
    key = _stage(adapter, body)

    staged = adapter.complete_staged_upload(CompleteMediaUpload(upload_id="up", staged_object_key=key))
    assert staged.byte_size == len(body)
    assert staged.sniffed_mime_type == "application/pdf"
    assert len(staged.content_hash) == 64

    stat = adapter.stat(key)
    assert stat.is_present is True and stat.byte_size == len(body) and stat.content_hash == staged.content_hash

    with adapter.open_stream(key) as full:
        assert full.read() == body
    with adapter.open_stream(key, ByteRange(start=9, end=13)) as ranged:
        assert ranged.read() == b"hello"


def test_s3_media_stat_reports_missing_blob_as_absent(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    stat = adapter.stat(f"{adapter.config.prefix}/tenant-demo/ms-1/{uuid4().hex}/missing.pdf")
    assert stat.is_present is False
    assert stat.byte_size == 0
    assert stat.content_hash == ""


def test_s3_media_stat_detects_overwritten_blob_corruption(minio_server: MinioServer) -> None:
    # §6.2: a COMMITTED version's content_hash must stop matching if the bytes change.
    adapter = _adapter(minio_server)
    key = _stage(adapter, b"%PDF-1.4 original")
    original = adapter.stat(key).content_hash
    adapter.write_staged(key, io.BytesIO(b"%PDF-1.4 tampered-and-longer"))
    assert adapter.stat(key).content_hash != original


def test_s3_media_write_failure_is_typed_retryable_error(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server, client=_WriteTimeoutClient(_s3_client(minio_server)))
    key = _initiate(adapter)
    with pytest.raises(AdapterError) as excinfo:
        adapter.write_staged(key, io.BytesIO(b"%PDF-1.4 hello"))
    failure = excinfo.value.failure
    assert failure.operation == "write_staged"
    assert failure.kind == "timeout"
    assert failure.is_retryable is True


def test_s3_media_delete_uncommitted_aborts_interrupted_multipart(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    key = f"{adapter.config.prefix}/tenant-demo/ms-1/{uuid4().hex}/big.pdf"
    created = adapter._client.create_multipart_upload(Bucket=adapter.config.bucket, Key=key)
    adapter._client.upload_part(
        Bucket=adapter.config.bucket, Key=key, UploadId=created["UploadId"], PartNumber=1, Body=b"x" * 16
    )

    adapter.delete_uncommitted(key)

    uploads = adapter._client.list_multipart_uploads(Bucket=adapter.config.bucket, Prefix=key).get("Uploads") or []
    assert all(upload.get("Key") != key for upload in uploads)
    assert adapter.stat(key).is_present is False


def test_s3_media_concurrent_same_path_uploads_keep_distinct_keys(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    first = _stage(adapter, b"%PDF-1.4 first", logical_path="/contracts/acme.pdf")
    second = _stage(adapter, b"%PDF-1.4 second", logical_path="/contracts/acme.pdf")
    assert first != second
    assert adapter.stat(first).content_hash != adapter.stat(second).content_hash


def test_s3_media_retry_write_same_key_is_idempotent(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    key = _stage(adapter, b"%PDF-1.4 hello")
    first = adapter.stat(key)
    adapter.write_staged(key, io.BytesIO(b"%PDF-1.4 hello"))
    assert adapter.stat(key).content_hash == first.content_hash


def test_s3_media_read_grant_is_scoped_to_one_key_with_expiry(minio_server: MinioServer) -> None:
    # M-T0-010: a grant is for exactly one object key, time-bounded, and a different
    # version resolves to a different key (the key namespace pins tenant + version).
    adapter = _adapter(minio_server)
    body = b"%PDF-1.4 grant-me"
    key = _stage(adapter, body)
    other_key = _stage(adapter, b"%PDF-1.4 other-version", logical_path="/a.pdf")

    grant = adapter.issue_read_grant(
        MediaReadGrantRequest(tenant_id="tenant-demo", media_item_version_id="v1", object_key=key)
    )
    other = adapter.issue_read_grant(
        MediaReadGrantRequest(tenant_id="tenant-demo", media_item_version_id="v2", object_key=other_key)
    )

    assert grant.grant_kind == "presigned-url"
    assert grant.expires_at is not None
    assert grant.url is not None and key in grant.url
    assert grant.url != other.url
    with urlopen(grant.url, timeout=5) as response:  # noqa: S310 - MinIO test endpoint
        assert response.read() == body


def test_s3_media_commit_reference_is_identity_without_copy(minio_server: MinioServer) -> None:
    # Metadata-pointer commit: the blob stays at its immutable key; commit re-stats it.
    adapter = _adapter(minio_server)
    body = b"%PDF-1.4 immutable"
    key = _stage(adapter, body)
    committed = adapter.commit_reference(key, key)
    assert committed.object_key == key
    assert committed.byte_size == len(body)
    assert adapter.stat(key).is_present is True


def test_s3_media_commit_reference_copies_to_distinct_key(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    body = b"%PDF-1.4 copy-me"
    staged = _stage(adapter, body)
    committed_key = f"{adapter.config.prefix}/tenant-demo/ms-1/{uuid4().hex}/committed.pdf"
    committed = adapter.commit_reference(staged, committed_key)
    assert committed.object_key == committed_key
    assert adapter.stat(committed_key).is_present is True


def test_s3_media_initiate_large_upload_uses_multipart_targets(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server)
    session = adapter.initiate_upload(
        InitiateMediaUpload(
            tenant_id="tenant-demo",
            media_set_id="ms-1",
            logical_path="/big.pdf",
            supplied_mime_type="application/pdf",
            declared_byte_size=64 * 1024 * 1024,
        )
    )
    assert session.upload_targets is not None and len(session.upload_targets) == 1
    uploads = (
        adapter._client.list_multipart_uploads(Bucket=adapter.config.bucket, Prefix=session.staged_object_key).get(
            "Uploads"
        )
        or []
    )
    assert any(upload.get("Key") == session.staged_object_key for upload in uploads)
    adapter.delete_uncommitted(session.staged_object_key)


@pytest.mark.parametrize(
    ("body", "expected_mime"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"plain text bytes", "application/octet-stream"),
    ],
)
def test_s3_media_complete_sniffs_known_magic_bytes(minio_server: MinioServer, body: bytes, expected_mime: str) -> None:
    adapter = _adapter(minio_server)
    key = _stage(adapter, body)
    staged = adapter.complete_staged_upload(CompleteMediaUpload(upload_id="up", staged_object_key=key))
    assert staged.sniffed_mime_type == expected_mime


def test_s3_media_read_grant_failure_is_typed_error(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server, client=_GrantFailClient(_s3_client(minio_server)))
    with pytest.raises(AdapterError) as excinfo:
        adapter.issue_read_grant(
            MediaReadGrantRequest(tenant_id="tenant-demo", media_item_version_id="v", object_key="tests/k.pdf")
        )
    assert excinfo.value.failure.operation == "issue_read_grant"
    assert excinfo.value.failure.kind == "unavailable"


def test_s3_media_stat_wraps_forbidden_error_as_unavailable(minio_server: MinioServer) -> None:
    adapter = _adapter(minio_server, client=_AccessForbiddenGetClient(_s3_client(minio_server)))
    with pytest.raises(AdapterError) as excinfo:
        adapter.stat("tests/forbidden/key.pdf")
    assert excinfo.value.failure.operation == "stat"
    assert excinfo.value.failure.kind == "unavailable"


def _adapter(minio_server: MinioServer, *, client: Any | None = None) -> S3MediaStorageAdapter:
    return S3MediaStorageAdapter(
        S3MediaStorageConfig(
            bucket=f"flite-media-{uuid4().hex}",
            endpoint_url=minio_server.endpoint_url,
            access_key_id=MINIO_ACCESS_KEY,
            secret_access_key=MINIO_SECRET_KEY,
            prefix=f"tests/{uuid4().hex}",
        ),
        client=client or _s3_client(minio_server),
    )


def _s3_client(minio_server: MinioServer) -> Any:
    boto3 = import_module("boto3")
    botocore_config = import_module("botocore.config")
    return boto3.client(
        "s3",
        endpoint_url=minio_server.endpoint_url,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=botocore_config.Config(s3={"addressing_style": "path"}),
    )


def _minio_ready(endpoint_url: str) -> bool:
    try:
        with urlopen(f"{endpoint_url}/minio/health/ready", timeout=2) as response:  # noqa: S310
            response.read()
            return response.status == 200
    except (ConnectionError, TimeoutError, URLError, json.JSONDecodeError):
        return False


def _wait_until(condition: Callable[[], bool], *, timeout_seconds: float = 60, interval_seconds: float = 1) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if condition():
            return
        sleep(interval_seconds)
    raise TimeoutError("condition not met before timeout")
