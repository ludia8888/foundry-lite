"""Object-link indexing methods for the object-index rebuild path.

Extracted from ``indexing_rebuild_service`` as a mixin so that module stays
under the application module-size budget. ``ObjectIndexingRebuildMixin`` inherits
this mixin, so the combined indexing service exposes all rebuild + link methods.
"""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    LinkTypeRow,
    ObjectIndexLinkRow,
    ObjectIndexRepository,
    ObjectLinkSourceDeletion,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.object_store.index_records import build_object_link_insert
from foundry_lite.application.services.object_store.indexing_protocols import IndexOntologyLookup
from foundry_lite.application.services.object_store.indexing_types import ObjectIndexRebuildPlan
from foundry_lite.domain.context import RequestContext


class ObjectIndexingLinkMixin:
    """Object-link indexing methods shared into the rebuild service."""

    object_index_repository: ObjectIndexRepository
    ontology_service: IndexOntologyLookup

    def _index_links_for_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> int:
        """Upsert all source-derived links for the object type and prune missing ones."""
        active = self.ontology_service._active_ontology_version(conn, ctx)
        links = self.object_index_repository.link_types_for_object_type(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            from_object_type_id=plan.object_type["id"],
        )
        source_link_keys: set[tuple[str, str, str]] = set()
        links_upserted = 0
        for link in links:
            for row in rows:
                link_key = self._source_link_key(link, row)
                if link_key is None:
                    continue
                source_link_keys.add(link_key)
                links_upserted += self._index_link_row(conn, ctx, plan, link, link_key)
        self._delete_missing_source_links(conn, ctx, plan, source_link_keys)
        return links_upserted

    def _source_link_key(self, link: LinkTypeRow, row: TabularRow) -> tuple[str, str, str] | None:
        """Return the (link_type, from, to) key for a source row, or None if incomplete."""
        from_id = row.get(link["backing"]["fromKey"])
        to_id = row.get(link["backing"]["toKey"])
        if from_id in {None, ""} or to_id in {None, ""}:
            return None
        return link["id"], str(from_id), str(to_id)

    def _index_link_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
        link_key: tuple[str, str, str],
    ) -> int:
        """Upsert a single object link, refreshing it if it already exists."""
        _, from_id, to_id = link_key
        existing = self.object_index_repository.object_link(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            link_type_id=link["id"],
            from_object_id=str(from_id),
            to_object_id=str(to_id),
            index_version=plan.index_version,
        )
        if existing:
            self._refresh_existing_link(conn, ctx, plan, existing)
            return 1
        self._insert_new_link(conn, ctx, plan, link, str(from_id), str(to_id))
        return 1

    def _delete_missing_source_links(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        source_link_keys: set[tuple[str, str, str]],
    ) -> None:
        """Mark links deleted when their source row is gone on a full rebuild."""
        if plan.mode != "full":
            return
        links = self.object_index_repository.object_links_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            from_object_type_id=plan.object_type["id"],
            index_version=plan.index_version,
        )
        for link in links:
            link_key = (link["link_type_id"], link["from_object_id"], link["to_object_id"])
            if link["deleted"] or link_key in source_link_keys:
                continue
            self.object_index_repository.mark_object_link_deleted_from_source(
                transaction=conn,
                record=ObjectLinkSourceDeletion(
                    link_id=link["id"],
                    tenant_id=ctx.tenant_id,
                    source_dataset_version_id=plan.source_dataset_version_id,
                    link_version=int(link["link_version"]) + 1,
                    deletion_reason="source_missing",
                    updated_at=_now(),
                ),
            )

    def _refresh_existing_link(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        existing: ObjectIndexLinkRow,
    ) -> None:
        """Bump an existing link's version and source pointer."""
        self.object_index_repository.refresh_object_link(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            link_id=existing["id"],
            link_version=existing["link_version"] + 1,
            source_dataset_version_id=plan.source_dataset_version_id,
            updated_at=_now(),
        )

    def _insert_new_link(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
        from_id: str,
        to_id: str,
    ) -> None:
        """Insert a new object link from a source row."""
        self.object_index_repository.insert_object_link(
            transaction=conn,
            record=build_object_link_insert(
                ctx,
                link,
                from_id,
                to_id,
                plan.source_dataset_version_id,
                index_version=plan.index_version,
                is_active=plan.mode == "full",
            ),
        )
