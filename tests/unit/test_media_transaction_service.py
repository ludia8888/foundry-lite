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
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.references import MediaReferenceService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService, StagedUpload
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaRepository
from sqlalchemy import create_engine

_FUTURE = "2099-01-01T00:00:00Z"


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
    reference = MediaReferenceService(engine=engine, media_repository=repo, media_storage=storage)
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
