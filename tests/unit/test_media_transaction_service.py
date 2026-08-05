"""M-T0 transaction-core invariants for the local Media/Content Plane (ADR-0001).

These assemble the media services directly over a real SQLite engine + local storage
adapter, binding a fake runtime so a commit-time audit/outbox failure can be injected.
That proves the metadata-pointer commit is atomic: a failure leaves nothing visible and
the staged blobs become sweepable orphans.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_repository import MediaItemRecord, MediaItemVersionRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.references import MediaReferenceService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService, StagedUpload
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
)
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_FUTURE = "2099-01-01T00:00:00Z"


class _NoHolderObjectQuery:
    """Attachment holder lookup that never resolves.

    These transaction tests upload normal media, never ``attachment``-kind parameters, so
    ``has_visible_attachment_holder`` short-circuits before this is consulted. Failing the
    lookup keeps that assumption honest: if a test ever did stage an attachment, it would
    surface as an unreachable holder instead of silently passing the access check.
    """

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> object:
        raise NotFound(
            "no attachment holder in this fixture",
            details={"objectType": object_type_api_name, "objectId": object_id},
        )


class _FakeRuntime:
    """Records audit/outbox writes; ``fail_outbox`` injects a commit-time failure."""

    def __init__(self) -> None:
        self.audits: list[str] = []
        self.outboxes: list[str] = []
        self.fail_outbox = False

    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        self.audits.append(event_type)

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None:
        if self.fail_outbox:
            raise RuntimeError("injected outbox failure")
        self.outboxes.append(event_type)
        return event_type


@dataclass(frozen=True)
class _MediaEnv:
    ctx: RequestContext
    repo: SqlAlchemyMediaRepository
    storage: LocalMediaStorageAdapter
    catalog: MediaCatalogService
    upload: MediaUploadService
    transaction: MediaTransactionService
    reference: MediaReferenceService
    runtime: _FakeRuntime
    media_set_id: str

    def open_tx(self, idempotency_key: str) -> str:
        return self.transaction.open(self.ctx, media_set_id=self.media_set_id, idempotency_key=idempotency_key)

    def _inputs(self, tx_id: str, logical_path: str) -> MediaUploadInput:
        return MediaUploadInput(
            media_set_id=self.media_set_id,
            media_transaction_id=tx_id,
            logical_path=logical_path,
            supplied_mime_type="application/pdf",
            schema_type="document",
            format="pdf",
            security_envelope={"tenantId": self.ctx.tenant_id, "classification": "confidential"},
        )

    def stage(self, tx_id: str, *, logical_path: str, body: bytes) -> StagedUpload:
        session = self.upload.initiate(
            self.ctx, media_set_id=self.media_set_id, logical_path=logical_path, supplied_mime_type="application/pdf"
        )
        return self.upload.complete(
            self.ctx, inputs=self._inputs(tx_id, logical_path), upload=session, source=io.BytesIO(body)
        )


@pytest.fixture
def media(tmp_path: Path) -> _MediaEnv:
    engine = create_engine(f"sqlite:///{tmp_path / 'media.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media-storage")
    runtime = _FakeRuntime()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    reference = MediaReferenceService(
        engine=engine,
        policy=PolicyService(),
        media_repository=repo,
        media_reference_binding_repository=SqlAlchemyMediaReferenceBindingRepository(engine),
        media_storage=storage,
    )
    reference.bind_collaborators({"object_query_service": _NoHolderObjectQuery()})
    ctx = RequestContext(roles=("admin",))
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
    return _MediaEnv(
        ctx=ctx,
        repo=repo,
        storage=storage,
        catalog=catalog,
        upload=upload,
        transaction=transaction,
        reference=reference,
        runtime=runtime,
        media_set_id=media_set.media_set_id,
    )


def test_staged_version_is_not_visible_until_commit(media: _MediaEnv) -> None:
    # M-T0-006: a STAGED version is not a resolvable reference; commit makes it visible.
    tx = media.open_tx("idem-1")
    staged = media.stage(tx, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 a")

    with pytest.raises(NotFound):
        media.reference.resolve(media.ctx, media_item_version_id=staged.media_item_version_id)

    media.transaction.commit(media.ctx, media_transaction_id=tx)
    resolved = media.reference.resolve(media.ctx, media_item_version_id=staged.media_item_version_id)
    assert resolved.version.status == "COMMITTED"


def test_commit_failure_leaves_nothing_visible_and_leaves_orphan_evidence(media: _MediaEnv) -> None:
    # M-T0-001: blob written, DB commit fails -> not visible; staged blob is a sweepable orphan.
    tx = media.open_tx("idem-1")
    staged = media.stage(tx, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 a")

    media.runtime.fail_outbox = True
    with pytest.raises(RuntimeError):
        media.transaction.commit(media.ctx, media_transaction_id=tx)

    # The whole commit rolled back: still STAGED, still not resolvable.
    with pytest.raises(NotFound):
        media.reference.resolve(media.ctx, media_item_version_id=staged.media_item_version_id)
    assert media.runtime.outboxes == []

    media.transaction.abort(media.ctx, media_transaction_id=tx, error={"reason": "commit_failed"})
    cleaned = media.transaction.sweep_orphans(media.ctx, older_than=_FUTURE)
    assert staged.blob_key in cleaned


def test_commit_after_failure_can_succeed_on_retry(media: _MediaEnv) -> None:
    tx = media.open_tx("idem-1")
    staged = media.stage(tx, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 a")

    media.runtime.fail_outbox = True
    with pytest.raises(RuntimeError):
        media.transaction.commit(media.ctx, media_transaction_id=tx)

    media.runtime.fail_outbox = False
    result = media.transaction.commit(media.ctx, media_transaction_id=tx)
    assert result.committed_version_ids == (staged.media_item_version_id,)


def test_retry_upload_is_idempotent_single_version(media: _MediaEnv) -> None:
    # M-T0-004: replaying complete for one upload session resolves to the same version.
    tx = media.open_tx("idem-1")
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/a.pdf",
        supplied_mime_type="application/pdf",
    )

    def _complete() -> StagedUpload:
        return media.upload.complete(
            media.ctx, inputs=media._inputs(tx, "/contracts/a.pdf"), upload=session, source=io.BytesIO(b"%PDF-1.4 a")
        )

    first = _complete()
    replay = _complete()
    assert first.is_duplicate is False
    assert replay.is_duplicate is True
    assert replay.media_item_version_id == first.media_item_version_id

    result = media.transaction.commit(media.ctx, media_transaction_id=tx)
    assert result.committed_version_ids == (first.media_item_version_id,)


def test_batch_commit_is_all_or_nothing(media: _MediaEnv) -> None:
    # M-T0-005: a transaction with two versions commits both or neither.
    tx = media.open_tx("idem-1")
    one = media.stage(tx, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 a")
    two = media.stage(tx, logical_path="/contracts/b.pdf", body=b"%PDF-1.4 b")

    media.runtime.fail_outbox = True
    with pytest.raises(RuntimeError):
        media.transaction.commit(media.ctx, media_transaction_id=tx)
    for staged in (one, two):
        with pytest.raises(NotFound):
            media.reference.resolve(media.ctx, media_item_version_id=staged.media_item_version_id)

    media.runtime.fail_outbox = False
    result = media.transaction.commit(media.ctx, media_transaction_id=tx)
    assert set(result.committed_version_ids) == {one.media_item_version_id, two.media_item_version_id}
    for staged in (one, two):
        assert media.reference.resolve(
            media.ctx, media_item_version_id=staged.media_item_version_id
        ).version.status == ("COMMITTED")


def test_concurrent_same_path_commits_keep_both_versions(media: _MediaEnv) -> None:
    # M-T0-003: two transactions writing the same path keep both versions; head CAS picks one.
    tx1 = media.open_tx("idem-1")
    tx2 = media.open_tx("idem-2")
    v1 = media.stage(tx1, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 first")
    v2 = media.stage(tx2, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 second")
    assert v1.media_item_id == v2.media_item_id
    assert v2.version_number == v1.version_number + 1

    media.transaction.commit(media.ctx, media_transaction_id=tx1)
    second = media.transaction.commit(media.ctx, media_transaction_id=tx2)

    # Neither version is lost: both resolve as COMMITTED with their own immutable bytes.
    first_resolved = media.reference.resolve(media.ctx, media_item_version_id=v1.media_item_version_id)
    second_resolved = media.reference.resolve(media.ctx, media_item_version_id=v2.media_item_version_id)
    assert first_resolved.version.content_hash != second_resolved.version.content_hash
    assert second.head_version_id_by_item[v2.media_item_id] == v2.media_item_version_id


def test_commit_is_idempotent_when_already_committed(media: _MediaEnv) -> None:
    tx = media.open_tx("idem-1")
    staged = media.stage(tx, logical_path="/contracts/a.pdf", body=b"%PDF-1.4 a")
    first = media.transaction.commit(media.ctx, media_transaction_id=tx)
    media.runtime.outboxes.clear()

    replay = media.transaction.commit(media.ctx, media_transaction_id=tx)
    assert replay.committed_version_ids == first.committed_version_ids == (staged.media_item_version_id,)
    # A replay of an already-committed transaction re-emits no events.
    assert media.runtime.outboxes == []


def test_upload_rejects_missing_transaction_before_storage_write(media: _MediaEnv) -> None:
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/missing-tx.pdf",
        supplied_mime_type="application/pdf",
    )

    with pytest.raises(NotFound):
        media.upload.complete(
            media.ctx,
            inputs=media._inputs("mtx-missing", "/contracts/missing-tx.pdf"),
            upload=session,
            source=io.BytesIO(b"%PDF-1.4 missing"),
        )

    assert media.storage.stat(session.staged_object_key).is_present is False


def test_upload_rejects_closed_transaction_before_storage_write(media: _MediaEnv) -> None:
    tx = media.open_tx("idem-closed")
    media.transaction.abort(media.ctx, media_transaction_id=tx, error={"reason": "closed"})
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/closed.pdf",
        supplied_mime_type="application/pdf",
    )

    with pytest.raises(ConflictDetected, match="not open"):
        media.upload.complete(
            media.ctx,
            inputs=media._inputs(tx, "/contracts/closed.pdf"),
            upload=session,
            source=io.BytesIO(b"%PDF-1.4 closed"),
        )

    assert media.storage.stat(session.staged_object_key).is_present is False


def test_upload_rejects_transaction_from_different_media_set_before_storage_write(media: _MediaEnv) -> None:
    other = media.catalog.create_media_set(
        media.ctx,
        MediaSetSpec(
            namespace="legal",
            name="images",
            schema_type="image",
            primary_format="png",
            allowed_input_formats=("png",),
            classification="confidential",
        ),
    )
    other_tx = media.transaction.open(media.ctx, media_set_id=other.media_set_id, idempotency_key="idem-other-set")
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/wrong-set.pdf",
        supplied_mime_type="application/pdf",
    )

    with pytest.raises(ConflictDetected, match="different media set"):
        media.upload.complete(
            media.ctx,
            inputs=media._inputs(other_tx, "/contracts/wrong-set.pdf"),
            upload=session,
            source=io.BytesIO(b"%PDF-1.4 wrong set"),
        )

    assert media.storage.stat(session.staged_object_key).is_present is False


def test_catalog_lookup_does_not_expose_other_tenant_media_set(media: _MediaEnv) -> None:
    other_ctx = RequestContext(tenant_id="tenant-other", actor_user_id="user-other")
    other = media.catalog.create_media_set(
        other_ctx,
        MediaSetSpec(
            namespace="legal",
            name="other-contracts",
            schema_type="document",
            primary_format="pdf",
            allowed_input_formats=("pdf",),
            classification="confidential",
        ),
    )

    with pytest.raises(NotFound, match="media set not found"):
        media.catalog.get_media_set(media.ctx, media_set_id=other.media_set_id)


def test_upload_rejects_schema_type_mismatch_before_storage_write(media: _MediaEnv) -> None:
    tx = media.open_tx("idem-schema-mismatch")
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/schema-mismatch.png",
        supplied_mime_type="image/png",
    )

    with pytest.raises(ValidationFailed, match="schema type does not match"):
        media.upload.complete(
            media.ctx,
            inputs=MediaUploadInput(
                media_set_id=media.media_set_id,
                media_transaction_id=tx,
                logical_path="/contracts/schema-mismatch.png",
                supplied_mime_type="image/png",
                schema_type="image",
                format="png",
                security_envelope={"tenantId": media.ctx.tenant_id, "classification": "confidential"},
            ),
            upload=session,
            source=io.BytesIO(b"not a contract pdf"),
        )

    assert media.storage.stat(session.staged_object_key).is_present is False


def test_upload_rejects_disallowed_format_before_storage_write(media: _MediaEnv) -> None:
    tx = media.open_tx("idem-format-mismatch")
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/format-mismatch.txt",
        supplied_mime_type="text/plain",
    )

    with pytest.raises(ValidationFailed, match="format is not allowed"):
        media.upload.complete(
            media.ctx,
            inputs=MediaUploadInput(
                media_set_id=media.media_set_id,
                media_transaction_id=tx,
                logical_path="/contracts/format-mismatch.txt",
                supplied_mime_type="text/plain",
                schema_type="document",
                format="txt",
                security_envelope={"tenantId": media.ctx.tenant_id, "classification": "confidential"},
            ),
            upload=session,
            source=io.BytesIO(b"not an allowed contract format"),
        )

    assert media.storage.stat(session.staged_object_key).is_present is False


def test_upload_deletes_staged_blob_when_metadata_insert_fails(
    media: _MediaEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = media.open_tx("idem-insert-fails")
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/insert-fails.pdf",
        supplied_mime_type="application/pdf",
    )

    def fail_insert_version(**_kwargs: object) -> object:
        raise RuntimeError("metadata insert failed")

    monkeypatch.setattr(media.repo, "insert_version", fail_insert_version)

    with pytest.raises(RuntimeError, match="metadata insert failed"):
        media.upload.complete(
            media.ctx,
            inputs=media._inputs(tx, "/contracts/insert-fails.pdf"),
            upload=session,
            source=io.BytesIO(b"%PDF-1.4 insert fails"),
        )

    assert media.storage.stat(session.staged_object_key).is_present is False


def test_upload_deletes_new_blob_when_repository_returns_existing_winner(
    media: _MediaEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = media.open_tx("idem-existing-winner")
    first = media.stage(tx, logical_path="/contracts/winner.pdf", body=b"%PDF-1.4 first")
    with media.upload.engine.begin() as conn:
        existing = media.repo.media_item_version_by_id(
            transaction=conn,
            tenant_id=media.ctx.tenant_id,
            media_item_version_id=first.media_item_version_id,
        )
    assert existing is not None
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/winner.pdf",
        supplied_mime_type="application/pdf",
    )

    def return_existing_version(**_kwargs: object) -> object:
        return existing

    monkeypatch.setattr(media.repo, "insert_version", return_existing_version)

    duplicate = media.upload.complete(
        media.ctx,
        inputs=media._inputs(tx, "/contracts/winner.pdf"),
        upload=session,
        source=io.BytesIO(b"%PDF-1.4 second"),
    )

    assert duplicate.is_duplicate is True
    assert duplicate.media_item_version_id == first.media_item_version_id
    assert media.storage.stat(session.staged_object_key).is_present is False


def test_persist_refuses_staged_insert_when_tx_committed_between_check_and_insert(
    media: _MediaEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    # STAGED-under-COMMITTED TOCTOU: the open-check passes, then commit() lands, then the
    # staged insert runs. A guarded write must re-read the tx status and refuse the insert,
    # so no version is ever staged under an already-COMMITTED transaction.
    tx = media.open_tx("idem-toctou")
    session = media.upload.initiate(
        media.ctx,
        media_set_id=media.media_set_id,
        logical_path="/contracts/a.pdf",
        supplied_mime_type="application/pdf",
    )
    original_complete = media.storage.complete_staged_upload

    def commit_then_complete(request: object) -> object:
        # Interleave: the transaction commits after the open-check, before the staged insert.
        media.transaction.commit(media.ctx, media_transaction_id=tx)
        return original_complete(request)  # type: ignore[arg-type]

    monkeypatch.setattr(media.storage, "complete_staged_upload", commit_then_complete)

    with pytest.raises(ConflictDetected, match="not open"):
        media.upload.complete(
            media.ctx,
            inputs=media._inputs(tx, "/contracts/a.pdf"),
            upload=session,
            source=io.BytesIO(b"%PDF-1.4 a"),
        )
    # The refused insert rolls back and the orphaned staged blob is cleaned up.
    assert media.storage.stat(session.staged_object_key).is_present is False


def test_commit_replay_does_not_commit_a_late_staged_version(media: _MediaEnv) -> None:
    # A version STAGED under an already-COMMITTED tx (the TOCTOU residue) must never be
    # silently flipped to COMMITTED by a commit replay: that would yield a committed version
    # with no head pointer and no outbox event (Invariant 10). The replay is read-only.
    tx = media.open_tx("idem-1")
    media.transaction.commit(media.ctx, media_transaction_id=tx)  # tx COMMITTED, zero versions
    now = _now()
    item_id = _new_id("mitem")
    version_id = _new_id("mver")
    with media.upload.engine.begin() as conn:
        media.repo.upsert_media_item(
            transaction=conn,
            record=MediaItemRecord(
                media_item_id=item_id,
                tenant_id=media.ctx.tenant_id,
                media_set_id=media.media_set_id,
                logical_path="/contracts/late.pdf",
                head_version_id=None,
                is_deleted=False,
                created_at=now,
                updated_at=now,
            ),
        )
        media.repo.insert_version(
            transaction=conn,
            record=MediaItemVersionRecord(
                media_item_version_id=version_id,
                tenant_id=media.ctx.tenant_id,
                media_item_id=item_id,
                media_transaction_id=tx,
                version_number=1,
                blob_key="staged/late",
                content_hash="late-hash",
                byte_size=1,
                supplied_mime_type="application/pdf",
                sniffed_mime_type="application/pdf",
                schema_type="document",
                format="pdf",
                probe_metadata={},
                security_envelope={},
                source_ref=None,
                status="STAGED",
                created_at=now,
            ),
        )

    media.runtime.outboxes.clear()
    media.transaction.commit(media.ctx, media_transaction_id=tx)  # replay

    with media.upload.engine.begin() as conn:
        version = media.repo.media_item_version_by_id(
            transaction=conn, tenant_id=media.ctx.tenant_id, media_item_version_id=version_id
        )
        item = media.repo.media_item_by_id(transaction=conn, tenant_id=media.ctx.tenant_id, media_item_id=item_id)
    assert version is not None and version.status == "STAGED"  # not silently committed
    assert item is not None and item.head_version_id is None  # no head advanced
    assert media.runtime.outboxes == []  # no outbox event re-emitted


def test_commit_unknown_transaction_raises_not_found(media: _MediaEnv) -> None:
    with pytest.raises(NotFound):
        media.transaction.commit(media.ctx, media_transaction_id="mtx-missing")


def test_abort_unknown_transaction_raises_conflict(media: _MediaEnv) -> None:
    with pytest.raises(ConflictDetected):
        media.transaction.abort(media.ctx, media_transaction_id="mtx-missing", error={"reason": "x"})


def test_sweep_with_no_orphans_returns_empty(media: _MediaEnv) -> None:
    assert media.transaction.sweep_orphans(media.ctx, older_than=_FUTURE) == []


def test_resolve_unknown_version_raises_not_found(media: _MediaEnv) -> None:
    with pytest.raises(NotFound):
        media.reference.resolve(media.ctx, media_item_version_id="mver-missing")


def test_create_media_set_rejects_non_transactional_policy(media: _MediaEnv) -> None:
    with pytest.raises(ValidationFailed):
        media.catalog.create_media_set(
            media.ctx,
            MediaSetSpec(
                namespace="legal",
                name="ledger",
                schema_type="document",
                primary_format="pdf",
                allowed_input_formats=("pdf",),
                classification="confidential",
                transaction_policy="transactionless",
            ),
        )


def test_get_media_set_unknown_raises_not_found(media: _MediaEnv) -> None:
    with pytest.raises(NotFound):
        media.catalog.get_media_set(media.ctx, media_set_id="mset-missing")
