"""Application service helpers for transactions workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord, MediaTransactionRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound


@dataclass(frozen=True)
class MediaCommitResult:
    """Outcome of an atomic metadata-pointer commit."""

    media_transaction_id: str
    committed_version_ids: tuple[str, ...]
    head_version_id_by_item: dict[str, str | None]
    committed_at: str | None


class MediaTransactionService(CoreService):
    """Media transaction lifecycle and the atomic metadata-pointer commit (doc §6.2).

    Serving truth is the metadata DB: a commit flips STAGED versions to COMMITTED, CASes
    each logical-path head, marks the transaction COMMITTED, and writes audit + outbox —
    all in one DB transaction. Any failure rolls the whole thing back, so nothing is ever
    partially visible; the staged blobs are then unreachable orphans for the sweeper.
    """

    required_dependencies = ("engine", "media_repository", "media_storage")
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def open(
        self,
        ctx: RequestContext,
        *,
        media_set_id: str,
        idempotency_key: str,
        mode: str = "APPEND",
        request_fingerprint: str = "",
    ) -> str:
        media_transaction_id = _new_id("mtx")
        record = MediaTransactionRecord(
            media_transaction_id=media_transaction_id,
            tenant_id=ctx.tenant_id,
            media_set_id=media_set_id,
            status="OPEN",
            mode=mode,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            opened_at=_now(),
        )
        with self.engine.begin() as conn:
            existing = self.media_repository.create_open_transaction(transaction=conn, record=record)
        return existing.media_transaction_id if existing is not None else media_transaction_id

    def commit(
        self,
        ctx: RequestContext,
        *,
        media_transaction_id: str,
        before_commit: Callable[[TransactionContext], None] | None = None,
    ) -> MediaCommitResult:
        with self.engine.begin() as conn:
            tx = self._require_transaction(conn, ctx, media_transaction_id)
            if tx.status == "COMMITTED":
                if before_commit is not None:
                    before_commit(conn)
                return self._committed_replay(conn, ctx, media_transaction_id)
            return self._commit_open_transaction(conn, ctx, media_transaction_id, before_commit)

    def _committed_replay(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        media_transaction_id: str,
    ) -> MediaCommitResult:
        committed = self.media_repository.fetch_committed_versions(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            media_transaction_id=media_transaction_id,
        )
        return self._result(media_transaction_id, committed, self._current_heads(conn, ctx, committed))

    def _commit_open_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        media_transaction_id: str,
        before_commit: Callable[[TransactionContext], None] | None,
    ) -> MediaCommitResult:
        committed_at = _now()
        committed = self.media_repository.commit_staged_versions(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            media_transaction_id=media_transaction_id,
            committed_at=committed_at,
        )
        heads = self._advance_heads(conn, ctx, committed, committed_at)
        if before_commit is not None:
            before_commit(conn)
        tx_after = self.media_repository.commit_transaction(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            media_transaction_id=media_transaction_id,
            committed_at=committed_at,
        )
        if tx_after is None or tx_after.status != "COMMITTED":
            raise ConflictDetected(
                "media transaction commit lost its OPEN state",
                details={"media_transaction_id": media_transaction_id},
            )
        self._emit_commit_events(conn, ctx, media_transaction_id, committed, heads)
        return self._result(media_transaction_id, committed, heads)

    def abort(self, ctx: RequestContext, *, media_transaction_id: str, error: dict[str, object] | None = None) -> None:
        with self.engine.begin() as conn:
            aborted = self.media_repository.abort_transaction(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                media_transaction_id=media_transaction_id,
                aborted_at=_now(),
                error=error,
            )
            if aborted is None or aborted.status != "ABORTED":
                raise ConflictDetected(
                    "media transaction abort lost its current state",
                    details={"media_transaction_id": media_transaction_id},
                )
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.transaction.aborted",
                resource_type="media_transaction",
                resource_id=media_transaction_id,
                action="abort",
                after_ref={"error": error or {}},
                correlation_id=ctx.request_id,
            )

    def sweep_orphans(self, ctx: RequestContext, *, older_than: str) -> list[str]:
        with self.engine.begin() as conn:
            orphans = self.media_repository.fetch_unreachable_staged_versions(
                transaction=conn, tenant_id=ctx.tenant_id, older_than=older_than
            )
            cleaned = [version.blob_key for version in orphans]
            for blob_key in cleaned:
                self.media_storage.delete_uncommitted(blob_key)
            if cleaned:
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="media.staging.swept",
                    resource_type="media_transaction",
                    resource_id=None,
                    action="purge",
                    after_ref={"blobKeys": cleaned},
                    correlation_id=ctx.request_id,
                )
        return cleaned

    def _require_transaction(
        self, conn: TransactionContext, ctx: RequestContext, media_transaction_id: str
    ) -> MediaTransactionRecord:
        tx = self.media_repository.transaction_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, media_transaction_id=media_transaction_id
        )
        if tx is None:
            raise NotFound("media transaction not found", details={"media_transaction_id": media_transaction_id})
        if tx.status not in {"OPEN", "COMMITTED"}:
            raise ConflictDetected(
                "media transaction is not open",
                details={"media_transaction_id": media_transaction_id, "status": tx.status},
            )
        return tx

    def _advance_heads(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        committed: list[MediaItemVersionRecord],
        updated_at: str,
    ) -> dict[str, str | None]:
        heads: dict[str, str | None] = {}
        for item_id, version in _latest_version_by_item(committed).items():
            item = self.media_repository.media_item_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_id=item_id
            )
            current_head = item.head_version_id if item is not None else None
            won = self.media_repository.cas_item_head_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                media_item_id=item_id,
                expected_head_version_id=current_head,
                new_head_version_id=version.media_item_version_id,
                updated_at=updated_at,
            )
            heads[item_id] = version.media_item_version_id if won is not None else current_head
        return heads

    def _current_heads(
        self, conn: TransactionContext, ctx: RequestContext, committed: list[MediaItemVersionRecord]
    ) -> dict[str, str | None]:
        heads: dict[str, str | None] = {}
        for item_id in {version.media_item_id for version in committed}:
            item = self.media_repository.media_item_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_id=item_id
            )
            heads[item_id] = item.head_version_id if item is not None else None
        return heads

    def _emit_commit_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        media_transaction_id: str,
        committed: list[MediaItemVersionRecord],
        heads: dict[str, str | None],
    ) -> None:
        version_ids = [version.media_item_version_id for version in committed]
        self.runtime_service._outbox(
            conn,
            ctx,
            "media.transaction.committed",
            "media_transaction",
            media_transaction_id,
            {"mediaTransactionId": media_transaction_id, "versionIds": version_ids, "heads": heads},
            idempotency_key=media_transaction_id,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="media.transaction.committed",
            resource_type="media_transaction",
            resource_id=media_transaction_id,
            action="commit",
            after_ref={"versionIds": version_ids, "heads": heads},
            correlation_id=ctx.request_id,
        )

    @staticmethod
    def _result(
        media_transaction_id: str, committed: list[MediaItemVersionRecord], heads: dict[str, str | None]
    ) -> MediaCommitResult:
        return MediaCommitResult(
            media_transaction_id=media_transaction_id,
            committed_version_ids=tuple(version.media_item_version_id for version in committed),
            head_version_id_by_item=heads,
            committed_at=max(
                (version.committed_at for version in committed if version.committed_at is not None),
                default=None,
            ),
        )


def _latest_version_by_item(
    committed: list[MediaItemVersionRecord],
) -> dict[str, MediaItemVersionRecord]:
    latest: dict[str, MediaItemVersionRecord] = {}
    for version in committed:
        current = latest.get(version.media_item_id)
        if current is None or version.version_number > current.version_number:
            latest[version.media_item_id] = version
    return latest
