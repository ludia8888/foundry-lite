"""Media retention/purge engine (L7): mark -> grace -> sweep, with fail-safe protection.

Mirrors Palantir Foundry retention (mark a version as a sweep candidate, wait out a grace
period, then physically sweep) plus the two protections that make a purge fail-safe:

* A version still **reachable** by a ``MediaReference`` binding is NEVER purged — like
  Foundry refusing to delete a still-referenced (latest) view of a dataset.
* A version under **legal hold** is NEVER purged, even past its grace period.

A purge is irreversible: it physically deletes the version row together with its derived-data
footprint (derivatives, content units, access caches) and the blob. If reachability cannot be
determined the version is skipped (fail-safe: never purge on uncertainty). The clock is
injected (``now``) — the service never reads it — so the sweep is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext


@dataclass(frozen=True)
class RetentionPurgeSummary:
    """Outcome of one sweep: ids purged plus the ids skipped and why (fail-safe evidence)."""

    purged_version_ids: tuple[str, ...]
    skipped_reachable_version_ids: tuple[str, ...]
    skipped_legal_hold_version_ids: tuple[str, ...]


class MediaRetentionService(CoreService):
    """Mark versions for retention, place/release legal holds, and sweep past-grace versions.

    The sweep is the only destructive surface; it refuses to purge any version still reachable
    by a MediaReference binding or under legal hold (invariant
    ``retention_never_purges_reachable_or_legal_hold_version``).
    """

    required_dependencies = ("engine", "media_repository", "media_storage")
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def mark_media_versions_for_retention(
        self, ctx: RequestContext, *, media_item_version_ids: list[str], now: datetime
    ) -> int:
        """Mark versions as sweep candidates (audited). Marked data is not serving truth."""
        marked_at = now.isoformat()
        with self.engine.begin() as conn:
            count = self.media_repository.mark_versions_for_retention(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                media_item_version_ids=media_item_version_ids,
                marked_at=marked_at,
            )
            self._audit(
                conn,
                ctx,
                event_type="media.retention.marked",
                action="mark",
                version_ids=media_item_version_ids,
            )
        return count

    def place_legal_hold(self, ctx: RequestContext, *, media_item_version_id: str) -> None:
        """Place a legal hold on one version; a held version is never purged."""
        self._set_legal_hold(ctx, media_item_version_id, has_legal_hold=True, event="media.retention.hold_placed")

    def release_legal_hold(self, ctx: RequestContext, *, media_item_version_id: str) -> None:
        """Release a legal hold; the version is again a normal sweep candidate."""
        self._set_legal_hold(ctx, media_item_version_id, has_legal_hold=False, event="media.retention.hold_released")

    def purge_marked_media_versions(
        self, ctx: RequestContext, *, now: datetime, grace: timedelta
    ) -> RetentionPurgeSummary:
        """Sweep: purge marked versions past their grace, skipping reachable/held ones."""
        cutoff = (now - grace).isoformat()
        with self.engine.begin() as conn:
            candidates = self.media_repository.fetch_retention_marked_versions(
                transaction=conn, tenant_id=ctx.tenant_id, marked_before=cutoff
            )
            purged: list[str] = []
            reachable: list[str] = []
            held: list[str] = []
            for version in candidates:
                self._sweep_one(conn, ctx, version, purged=purged, reachable=reachable, held=held)
        return RetentionPurgeSummary(
            purged_version_ids=tuple(purged),
            skipped_reachable_version_ids=tuple(reachable),
            skipped_legal_hold_version_ids=tuple(held),
        )

    def _sweep_one(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        version: MediaItemVersionRecord,
        *,
        purged: list[str],
        reachable: list[str],
        held: list[str],
    ) -> None:
        version_id = version.media_item_version_id
        if version.has_legal_hold:
            held.append(version_id)
            return
        if self._is_reachable(conn, ctx, version_id):
            reachable.append(version_id)
            return
        self._purge_one(conn, ctx, version)
        purged.append(version_id)

    def _is_reachable(self, conn: TransactionContext, ctx: RequestContext, version_id: str) -> bool:
        # Fail-safe: a binding row targeting this version means it is still reachable. Any
        # failure to determine this propagates as a rollback, so nothing is purged on doubt.
        return self.media_repository.has_reference_binding(
            transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=version_id
        )

    def _purge_one(self, conn: TransactionContext, ctx: RequestContext, version: MediaItemVersionRecord) -> None:
        version_id = version.media_item_version_id
        self.media_repository.purge_version(transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=version_id)
        self.media_storage.delete_uncommitted(version.blob_key)
        self._audit(
            conn,
            ctx,
            event_type="media.retention.purged",
            action="purge",
            version_ids=[version_id],
            resource_id=version_id,
        )

    def _set_legal_hold(
        self, ctx: RequestContext, media_item_version_id: str, *, has_legal_hold: bool, event: str
    ) -> None:
        with self.engine.begin() as conn:
            self.media_repository.set_version_legal_hold(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                media_item_version_id=media_item_version_id,
                has_legal_hold=has_legal_hold,
            )
            self._audit(conn, ctx, event_type=event, action="legal_hold", version_ids=[media_item_version_id])

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        action: str,
        version_ids: list[str],
        resource_id: str | None = None,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=event_type,
            resource_type="media_item_version",
            resource_id=resource_id,
            action=action,
            after_ref={"mediaItemVersionIds": version_ids},
            correlation_id=ctx.request_id,
        )
