"""L6 real external connector live test (Media/Content Plane, virtual media sets).

A virtual media set is a *pointer* to an object in an external source system — Foundry never
copies the bytes. This proves the real ``s3-external`` connector end-to-end against live MinIO:
an object PUT into an external bucket is HEAD'd through ``S3ExternalMediaReader`` (no byte copy
into Foundry), its real ETag is pinned on an ``is_virtual`` media set, and ``resolve_external_
version`` re-validates that ETag on every read. Because a virtual set "is not aware of source
updates/deletions", drift fails closed (``virtual_external_version_etag_drifted``); an explicitly
mutable reference instead surfaces freshness (``is_stale``) without copying or failing closed.

MinIO runs via testcontainers (available in the coverage lane, like the S3 media storage
integration tests); the test never skips, so the real connector glue stays measured.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from foundry_lite.application.ports.media_repository import MediaSetRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.media.virtual_sets import VirtualMediaSetService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_external_media_reader import (
    S3ExternalMediaReader,
    S3ExternalMediaReaderConfig,
)
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaRepository
from sqlalchemy import create_engine
from testcontainers.core.container import DockerContainer

MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


class _FakeRuntime:
    def _audit(self, *_: object, **__: object) -> None: ...

    def _outbox(self, *_: object, **__: object) -> str | None:
        return "evt"


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


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    reader: S3ExternalMediaReader
    bucket: str
    client: Any
    service: VirtualMediaSetService
    virtual_set_id: str


@pytest.fixture
def env(minio_server: MinioServer, tmp_path: Path) -> _Env:
    client = _s3_client(minio_server)
    bucket = f"flite-external-{uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    reader = S3ExternalMediaReader(
        S3ExternalMediaReaderConfig(
            endpoint_url=minio_server.endpoint_url,
            access_key_id=MINIO_ACCESS_KEY,
            secret_access_key=MINIO_SECRET_KEY,
        ),
        client=client,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'external.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    ctx = RequestContext()
    record = _virtual_media_set_record(ctx)
    with engine.begin() as conn:
        repo.create_media_set_or_get_existing(transaction=conn, record=record)
    service = VirtualMediaSetService(engine=engine, media_repository=repo, external_media_reader=reader)
    service.bind_collaborators({"runtime_service": _FakeRuntime()})
    return _Env(
        ctx=ctx, reader=reader, bucket=bucket, client=client, service=service, virtual_set_id=record.media_set_id
    )


@pytest.mark.integration_scenario("media-external")
def test_external_pinned_etag_resolves_against_live_object(env: _Env) -> None:
    # A real external object is HEAD'd (no byte copy into Foundry) and its ETag is pinned.
    key = f"contracts/{uuid4().hex}.pdf"
    uri = f"s3://{env.bucket}/{key}"
    env.client.put_object(Bucket=env.bucket, Key=key, Body=b"%PDF-1.4 external original")
    etag = env.reader.stat(_request(uri)).etag
    assert etag and '"' not in etag  # quotes stripped from the live S3 ETag

    version = env.service.register_external_version(
        env.ctx,
        media_set_id=env.virtual_set_id,
        logical_path="/external/a.pdf",
        source_ref={"uri": uri, "etag": etag},
    )
    # No bytes were copied into Foundry: the version blob_key is the external URI itself.
    assert version.blob_key == uri

    resolved = env.service.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)
    assert resolved.etag == etag
    assert resolved.is_stale is False


@pytest.mark.integration_scenario("media-external")
def test_external_etag_drift_fails_closed_when_source_overwritten(env: _Env) -> None:
    # Because a virtual set is not aware of source updates, an overwrite (new ETag) under a
    # pinned version must fail closed rather than silently read different bytes.
    key = f"contracts/{uuid4().hex}.pdf"
    uri = f"s3://{env.bucket}/{key}"
    env.client.put_object(Bucket=env.bucket, Key=key, Body=b"%PDF-1.4 original")
    pinned = env.reader.stat(_request(uri)).etag

    version = env.service.register_external_version(
        env.ctx,
        media_set_id=env.virtual_set_id,
        logical_path="/external/drift.pdf",
        source_ref={"uri": uri, "etag": pinned},
    )

    env.client.put_object(Bucket=env.bucket, Key=key, Body=b"%PDF-1.4 tampered-and-longer")
    assert env.reader.stat(_request(uri)).etag != pinned

    with pytest.raises(InvariantViolation) as excinfo:
        env.service.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)
    assert "virtual_external_version_etag_drifted" in str(excinfo.value)


@pytest.mark.integration_scenario("media-external")
def test_external_mutable_reference_surfaces_freshness_on_delete(env: _Env) -> None:
    # A mutable reference pins no ETag: freshness is surfaced (is_stale), never failed closed.
    key = f"live/{uuid4().hex}.pdf"
    uri = f"s3://{env.bucket}/{key}"
    env.client.put_object(Bucket=env.bucket, Key=key, Body=b"%PDF-1.4 live")

    version = env.service.register_external_version(
        env.ctx,
        media_set_id=env.virtual_set_id,
        logical_path="/external/live.pdf",
        source_ref={"uri": uri, "is_mutable_external_reference": True},
    )

    present = env.service.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)
    assert present.is_stale is False

    env.client.delete_object(Bucket=env.bucket, Key=key)
    after_delete = env.service.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)
    assert after_delete.is_stale is True


@pytest.mark.integration_scenario("media-external")
def test_external_unversioned_register_is_rejected(env: _Env) -> None:
    # Neither a pinned etag/version nor a mutable marker: rejected before any external read.
    uri = f"s3://{env.bucket}/unversioned/{uuid4().hex}.pdf"
    with pytest.raises(InvariantViolation) as excinfo:
        env.service.register_external_version(
            env.ctx,
            media_set_id=env.virtual_set_id,
            logical_path="/external/unversioned.pdf",
            source_ref={"uri": uri},
        )
    assert "virtual_external_version_unversioned" in str(excinfo.value)


def _request(uri: str) -> Any:
    from foundry_lite.application.ports.external_media_reader import ExternalReadRequest

    return ExternalReadRequest(uri=uri)


def _virtual_media_set_record(ctx: RequestContext) -> MediaSetRecord:
    now = _now()
    return MediaSetRecord(
        media_set_id=_new_id("mset"),
        tenant_id=ctx.tenant_id,
        namespace="external",
        name="docs",
        schema_type="document",
        primary_format="external",
        allowed_input_formats=("external",),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="local",
        classification="internal",
        retention_policy_id=None,
        created_at=now,
        updated_at=now,
        is_virtual=True,
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
