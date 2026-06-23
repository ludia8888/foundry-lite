"""M9 virtual media sets (invariant ``external_virtual_object_requires_version_or_mutable_marker``).

A virtual media set reads from an external source without copying bytes. An external version
must either pin an immutable etag/version (re-validated on resolve; a drift fails closed) or be
explicitly marked a mutable external reference (its freshness is surfaced). A version that does
neither is rejected — it must never silently read different bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.external_media_reader import (
    ExternalObjectStat,
    ExternalReadRequest,
)
from foundry_lite.application.ports.media_repository import MediaSetRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.media.virtual_sets import VirtualMediaSetService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaRepository


class _FakeRuntime:
    def _audit(self, *_: object, **__: object) -> None: ...

    def _outbox(self, *_: object, **__: object) -> str | None:
        return "evt"


class _FakeExternalReader:
    """An external reader whose current etag/presence is scripted by the test."""

    profile_name = "fake-external"

    def __init__(self, *, etag: str = "etag-1", is_present: bool = True) -> None:
        self._etag = etag
        self._is_present = is_present

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def stat(self, request: ExternalReadRequest) -> ExternalObjectStat:
        return ExternalObjectStat(uri=request.uri, etag=self._etag, byte_size=10, is_present=self._is_present)


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: object
    repo: SqlAlchemyMediaRepository
    virtual_set_id: str


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


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'virtual.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    ctx = RequestContext()
    record = _virtual_media_set_record(ctx)
    with engine.begin() as conn:
        repo.create_media_set_or_get_existing(transaction=conn, record=record)
    return _Env(ctx=ctx, engine=engine, repo=repo, virtual_set_id=record.media_set_id)


def _service(env: _Env, reader: object) -> VirtualMediaSetService:
    service = VirtualMediaSetService(engine=env.engine, media_repository=env.repo, external_media_reader=reader)
    service.bind_collaborators({"runtime_service": _FakeRuntime()})
    return service


def test_virtual_media_requires_version_or_mutable_marker(env: _Env) -> None:
    service = _service(env, _FakeExternalReader())
    with pytest.raises(InvariantViolation):
        service.register_external_version(
            env.ctx,
            media_set_id=env.virtual_set_id,
            logical_path="/external/a.pdf",
            source_ref={"uri": "s3://ext/a.pdf"},  # neither etag/version nor mutable marker
        )


def test_etag_pinned_version_resolves_when_unchanged(env: _Env) -> None:
    service = _service(env, _FakeExternalReader(etag="etag-1"))
    version = service.register_external_version(
        env.ctx,
        media_set_id=env.virtual_set_id,
        logical_path="/external/a.pdf",
        source_ref={"uri": "s3://ext/a.pdf", "etag": "etag-1"},
    )
    resolved = service.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)
    assert resolved.etag == "etag-1"
    assert resolved.is_stale is False


def test_virtual_etag_drift_fails_closed(env: _Env) -> None:
    register = _service(env, _FakeExternalReader(etag="etag-1"))
    version = register.register_external_version(
        env.ctx,
        media_set_id=env.virtual_set_id,
        logical_path="/external/a.pdf",
        source_ref={"uri": "s3://ext/a.pdf", "etag": "etag-1"},
    )
    drifted = _service(env, _FakeExternalReader(etag="etag-2"))  # source bytes changed under us
    with pytest.raises(InvariantViolation):
        drifted.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)


def test_mutable_marker_version_surfaces_freshness(env: _Env) -> None:
    service = _service(env, _FakeExternalReader(etag="etag-9", is_present=False))
    version = service.register_external_version(
        env.ctx,
        media_set_id=env.virtual_set_id,
        logical_path="/external/live.pdf",
        source_ref={"uri": "s3://ext/live.pdf", "is_mutable_external_reference": True},
    )
    resolved = service.resolve_external_version(env.ctx, media_item_version_id=version.media_item_version_id)
    assert resolved.etag == "etag-9"
    assert resolved.is_stale is True  # mutable reference: freshness surfaced, not failed closed


def test_register_rejects_non_virtual_media_set(env: _Env) -> None:
    ingest_record = MediaSetRecord(
        media_set_id=_new_id("mset"),
        tenant_id=env.ctx.tenant_id,
        namespace="brand",
        name="ingested",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="local",
        classification="internal",
        retention_policy_id=None,
        created_at=_now(),
        updated_at=_now(),
        is_virtual=False,
    )
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        env.repo.create_media_set_or_get_existing(transaction=conn, record=ingest_record)
    service = _service(env, _FakeExternalReader())
    with pytest.raises(ValidationFailed):
        service.register_external_version(
            env.ctx,
            media_set_id=ingest_record.media_set_id,
            logical_path="/x.pdf",
            source_ref={"uri": "s3://ext/x.pdf", "etag": "e"},
        )
