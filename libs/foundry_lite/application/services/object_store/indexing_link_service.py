"""Object-link indexing use cases for object and join-dataset rebuilds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetVersionRow,
    LinkTypeRow,
    ObjectIndexLinkRow,
    ObjectIndexRepository,
    ObjectLinkSourceDeletion,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.index_records import build_object_link_insert
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetTransactionFiles,
    IndexOntologyLookup,
)
from foundry_lite.application.services.object_store.indexing_types import ObjectIndexRebuildPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.object_store.indexing import (
    SOURCE_MISSING_DELETION_REASON,
    should_delete_missing_source_link,
    should_refresh_existing_link,
)


@dataclass(frozen=True)
class ObjectLinkIndexingResult:
    """Counts and exact join-source versions consumed by one object rebuild."""

    links_upserted: int
    source_dataset_version_ids: dict[str, str]


class ObjectLinkIndexingService(CoreService):
    """Indexes source-derived and join-dataset-backed object links."""

    required_dependencies = ("compute_adapter", "object_index_repository")
    required_collaborators = (
        "dataset_transaction_service",
        "ontology_service",
    )
    compute_adapter: ComputeAdapter
    dataset_transaction_service: IndexDatasetTransactionFiles
    object_index_repository: ObjectIndexRepository
    ontology_service: IndexOntologyLookup

    def _index_links_for_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        rows: Sequence[TabularRow],
    ) -> ObjectLinkIndexingResult:
        """Upsert outbound links and record every separate join-source version."""
        active = self.ontology_service._active_ontology_version(conn, ctx)
        links = self.object_index_repository.link_types_for_object_type(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            from_object_type_id=plan.object_type["id"],
        )
        links_upserted = 0
        join_source_versions: dict[str, str] = {}
        for link in links:
            link_rows, source_version_id = self._link_source_rows(conn, ctx, plan, link, rows)
            if link["cardinality"] == "many_to_many":
                join_source_versions[link["api_name"]] = source_version_id
            source_keys = self._source_link_keys(link, link_rows)
            for link_key in source_keys:
                links_upserted += self._index_link_row(conn, ctx, plan, link, link_key, source_version_id)
            self._delete_missing_source_links(conn, ctx, plan, link, source_keys, source_version_id)
        return ObjectLinkIndexingResult(links_upserted, join_source_versions)

    def _link_source_rows(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
        rows: Sequence[TabularRow],
    ) -> tuple[Sequence[TabularRow], str]:
        if link["cardinality"] != "many_to_many":
            return rows, plan.source_dataset_version_id
        version = self._pinned_join_dataset_version(plan, link)
        path = self.dataset_transaction_service._version_file_path(version)
        return self.compute_adapter.rows_from_parquet(path), version["id"]

    def _pinned_join_dataset_version(
        self,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
    ) -> DatasetVersionRow:
        source = plan.link_sources.get(link["api_name"])
        if source is None:
            raise ValidationFailed(
                "many-to-many link source was not pinned for index run",
                details={"linkType": link["api_name"], "indexRunId": plan.run_id},
            )
        return source.dataset_version

    def _source_link_keys(
        self,
        link: LinkTypeRow,
        rows: Sequence[TabularRow],
    ) -> set[tuple[str, str, str]]:
        keys: set[tuple[str, str, str]] = set()
        for row in rows:
            keys.update(self._source_link_keys_for_row(link, row))
        return keys

    def _source_link_keys_for_row(self, link: LinkTypeRow, row: TabularRow) -> set[tuple[str, str, str]]:
        """Return one key pair, or every pair for array-valued join endpoints."""
        from_key = link["backing"].get("fromKey")
        to_key = link["backing"].get("toKey")
        if not isinstance(from_key, str) or not isinstance(to_key, str):
            return set()
        from_ids = self._endpoint_ids(row.get(from_key), link["api_name"], from_key)
        to_ids = self._endpoint_ids(row.get(to_key), link["api_name"], to_key)
        if link["cardinality"] != "many_to_many":
            return {(link["id"], from_ids[0], to_ids[0])} if len(from_ids) == len(to_ids) == 1 else set()
        return {(link["id"], from_id, to_id) for from_id in from_ids for to_id in to_ids}

    def _endpoint_ids(self, value: object, link_type: str, column: str) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
        if isinstance(value, Mapping):
            raise ValidationFailed(
                "link endpoint struct requires a field selector",
                details={"linkType": link_type, "column": column},
            )
        values = value if isinstance(value, Sequence) and not isinstance(value, str | bytes) else (value,)
        return tuple(str(item) for item in values if item is not None and item != "")

    def _index_link_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link: LinkTypeRow,
        link_key: tuple[str, str, str],
        source_version_id: str,
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
        if should_refresh_existing_link(is_present=existing is not None):
            assert existing is not None
            self._refresh_existing_link(conn, ctx, existing, source_version_id)
            return 1
        self._insert_new_link(conn, ctx, plan, link, str(from_id), str(to_id), source_version_id)
        return 1

    def _delete_missing_source_links(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        link_type: LinkTypeRow,
        source_link_keys: set[tuple[str, str, str]],
        source_version_id: str,
    ) -> None:
        """Mark links deleted when their source row is gone on a full rebuild."""
        links = self.object_index_repository.object_links_for_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            from_object_type_id=plan.object_type["id"],
            index_version=plan.index_version,
        )
        for link in links:
            link_key = (link["link_type_id"], link["from_object_id"], link["to_object_id"])
            if (
                not should_delete_missing_source_link(
                    is_deleted=bool(link["deleted"]),
                    is_present_in_source=link_key in source_link_keys,
                    is_source_backed=link["source_dataset_version_id"] is not None,
                    index_mode=plan.mode,
                )
                or link["link_type_id"] != link_type["id"]
            ):
                continue
            self.object_index_repository.mark_object_link_deleted_from_source(
                transaction=conn,
                record=ObjectLinkSourceDeletion(
                    link_id=link["id"],
                    tenant_id=ctx.tenant_id,
                    source_dataset_version_id=source_version_id,
                    link_version=int(link["link_version"]) + 1,
                    deletion_reason=SOURCE_MISSING_DELETION_REASON,
                    updated_at=_now(),
                ),
            )

    def _refresh_existing_link(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: ObjectIndexLinkRow,
        source_version_id: str,
    ) -> None:
        """Bump an existing link's version and source pointer."""
        self.object_index_repository.refresh_object_link(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            link_id=existing["id"],
            link_version=existing["link_version"] + 1,
            source_dataset_version_id=source_version_id,
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
        source_version_id: str,
    ) -> None:
        """Insert a new object link from a source row."""
        self.object_index_repository.insert_object_link(
            transaction=conn,
            record=build_object_link_insert(
                ctx,
                link,
                from_id,
                to_id,
                source_version_id,
                index_version=plan.index_version,
                is_active=plan.mode == "full",
            ),
        )
