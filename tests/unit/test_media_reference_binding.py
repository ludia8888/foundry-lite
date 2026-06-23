"""M4 action-bound media-reference binding invariants (doc §1.6/§6.4/§12.1).

Assembles the media stack over SQLite + local storage with a fake runtime to prove the
binding unit of work: only a COMMITTED version binds (a rejected bind writes nothing),
the bind is idempotent, a reused key with a different version conflicts, an existing
reference stays pinned after a re-bind, and a reference is masked when the caller's
clearance does not cover its classification.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.media.binding import MediaReferenceBindingService
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
)
from sqlalchemy import create_engine


class _FakeRuntime:
    def __init__(self) -> None:
        self.audits: list[str] = []

    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        self.audits.append(event_type)

    def _outbox(self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object) -> str:
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    binding: MediaReferenceBindingService
    upload: MediaUploadService
    transaction: MediaTransactionService
    media_set_id: str

    def commit_version(self, *, logical_path: str, body: bytes, idem: str) -> str:
        tx = self.transaction.open(self.ctx, media_set_id=self.media_set_id, idempotency_key=idem)
        session = self.upload.initiate(
            self.ctx, media_set_id=self.media_set_id, logical_path=logical_path, supplied_mime_type="application/pdf"
        )
        staged = self._complete(tx, session, logical_path, body)
        self.transaction.commit(self.ctx, media_transaction_id=tx)
        return staged.media_item_version_id

    def stage_only(self, *, logical_path: str, body: bytes, idem: str) -> str:
        tx = self.transaction.open(self.ctx, media_set_id=self.media_set_id, idempotency_key=idem)
        session = self.upload.initiate(
            self.ctx, media_set_id=self.media_set_id, logical_path=logical_path, supplied_mime_type="application/pdf"
        )
        return self._complete(tx, session, logical_path, body).media_item_version_id

    def _complete(self, tx: str, session: object, logical_path: str, body: bytes):  # type: ignore[no-untyped-def]
        return self.upload.complete(
            self.ctx,
            inputs=MediaUploadInput(
                media_set_id=self.media_set_id,
                media_transaction_id=tx,
                logical_path=logical_path,
                supplied_mime_type="application/pdf",
                schema_type="document",
                format="pdf",
                security_envelope={"tenantId": self.ctx.tenant_id, "classification": "confidential"},
            ),
            upload=session,  # type: ignore[arg-type]
            source=io.BytesIO(body),
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'm4.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    binding_repo = SqlAlchemyMediaReferenceBindingRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    binding = MediaReferenceBindingService(
        engine=engine, media_repository=repo, media_reference_binding_repository=binding_repo
    )
    binding.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext()
    media_set = catalog.create_media_set(
        ctx,
        MediaSetSpec(
            namespace="legal",
            name="contracts",
            schema_type="document",
            primary_format="pdf",
            allowed_input_formats=("pdf",),
            classification="confidential",
        ),
    )
    return _Env(ctx=ctx, binding=binding, upload=upload, transaction=transaction, media_set_id=media_set.media_set_id)


def test_bind_commits_and_resolves_pinned_reference(env: _Env) -> None:
    version = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="t1")
    record = env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="sourceDocument",
        media_item_version_id=version,
        idempotency_key="bind-1",
    )
    assert record.media_item_version_id == version
    resolved = env.binding.resolve(env.ctx, holder_type="Order", holder_id="order-1", property_name="sourceDocument")
    assert resolved is not None and resolved.media_item_version_id == version


def test_bind_rejects_uncommitted_version_and_writes_nothing(env: _Env) -> None:
    staged = env.stage_only(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="t1")
    with pytest.raises(ConflictDetected):
        env.binding.bind(
            env.ctx,
            holder_type="Order",
            holder_id="order-1",
            property_name="sourceDocument",
            media_item_version_id=staged,
            idempotency_key="bind-1",
        )
    with pytest.raises(NotFound):
        env.binding.resolve(env.ctx, holder_type="Order", holder_id="order-1", property_name="sourceDocument")


def test_bind_is_idempotent_for_same_key_and_version(env: _Env) -> None:
    version = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="t1")
    first = env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=version,
        idempotency_key="k",
    )
    replay = env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=version,
        idempotency_key="k",
    )
    assert replay.media_reference_binding_id == first.media_reference_binding_id


def test_same_key_different_version_conflicts(env: _Env) -> None:
    v1 = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="t1")
    v2 = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 b", idem="t2")
    env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=v1,
        idempotency_key="k",
    )
    with pytest.raises(ConflictDetected):
        env.binding.bind(
            env.ctx,
            holder_type="Order",
            holder_id="order-1",
            property_name="doc",
            media_item_version_id=v2,
            idempotency_key="k",
        )


def test_rebind_repoints_but_pins_each_version(env: _Env) -> None:
    v1 = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="t1")
    v2 = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 b", idem="t2")
    env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=v1,
        idempotency_key="k1",
    )
    env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=v2,
        idempotency_key="k2",
    )
    resolved = env.binding.resolve(env.ctx, holder_type="Order", holder_id="order-1", property_name="doc")
    assert resolved is not None and resolved.media_item_version_id == v2


def test_resolve_masks_reference_outside_clearance(env: _Env) -> None:
    version = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="t1")
    env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=version,
        idempotency_key="k",
    )
    masked = env.binding.resolve(
        env.ctx, holder_type="Order", holder_id="order-1", property_name="doc", allowed_classifications=("public",)
    )
    cleared = env.binding.resolve(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        allowed_classifications=("confidential",),
    )
    assert masked is None
    assert cleared is not None and cleared.media_item_version_id == version
