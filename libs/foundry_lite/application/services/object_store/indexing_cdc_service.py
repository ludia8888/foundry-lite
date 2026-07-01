"""Application service helpers for indexing cdc service workflows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from foundry_lite.application.ports import (
    IndexRunCursor,
    IndexRunRecord,
    ObjectIndexCdcResult,
    ObjectIndexRepository,
    ObjectPropertyMap,
    ObjectRecordCdcUpdate,
    ObjectRecordInsert,
    ObjectRecordRow,
    ObjectTypeRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.object_store.cdc_indexing import (
    cdc_cursor,
    cdc_event_should_skip,
    cdc_property_versions,
    cdc_source_dataset,
    cdc_source_dataset_version_id,
    cdc_source_hash,
    parse_object_cdc_event,
)
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexObjectRecords,
    IndexOntologyLookup,
    IndexRuntimeBoundary,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectCdcEvent,
    ObjectIndexCdcCounts,
    ObjectIndexRebuildCounts,
    object_index_cdc_response,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class ObjectIndexingCdcMixin(ABC):
    engine: TransactionManager
    object_index_repository: ObjectIndexRepository
    object_records_service: IndexObjectRecords
    ontology_service: IndexOntologyLookup
    runtime_service: IndexRuntimeBoundary

    @abstractmethod
    def _mark_index_run_succeeded(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        counts: ObjectIndexRebuildCounts | ObjectIndexCdcCounts,
        cursor: IndexRunCursor | None = None,
    ) -> None: ...

    @abstractmethod
    def _mark_index_run_failed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        exc: Exception,
        adapter: str = "compute_adapter.rows_from_parquet",
    ) -> None: ...

    @abstractmethod
    def _record_conflicts_for_base_update(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        object_id: str,
        existing: ObjectRecordRow,
        base_patch: ObjectPropertyMap,
        source_dataset_version_id: str,
    ) -> None: ...

    @abstractmethod
    def _merge_properties(
        self,
        conn: TransactionContext,
        object_type_id: str,
        base: ObjectPropertyMap,
        edits: ObjectPropertyMap,
    ) -> ObjectPropertyMap: ...

    @abstractmethod
    def _emit_object_changed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        source_dataset_version_id: str,
    ) -> None: ...

    @abstractmethod
    def _require_object_index(self, ctx: RequestContext, resource_type: str, resource_id: str) -> None: ...

    def index_cdc_events(
        self,
        object_type_api_name: str,
        events: Sequence[ObjectPropertyMap],
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexCdcResult:
        ctx = ctx or RequestContext()
        self._require_object_index(ctx, "object_type", object_type_api_name)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="index_cdc_events",
            resource_type="object_type",
            resource_id=object_type_api_name,
        )
        with self.engine.begin() as conn:
            object_type = self._cdc_object_type(conn, ctx, object_type_api_name)
            run_id = self._create_cdc_index_run(conn, ctx, object_type, events)
        try:
            with self.engine.begin() as conn:
                object_type = self._cdc_object_type(conn, ctx, object_type_api_name)
                parsed = self._parse_cdc_events(conn, object_type, events)
                counts = self._persist_cdc_events(conn, ctx, object_type, parsed)
                cursor = cdc_cursor(parsed, counts.events_skipped)
                self._mark_index_run_succeeded(conn, ctx, run_id, counts, cursor=cursor)
                self._audit_cdc_index(conn, ctx, object_type, run_id, counts)
            return object_index_cdc_response(run_id, object_type_api_name, counts)
        except Exception as exc:
            with self.engine.begin() as conn:
                self._mark_index_run_failed(conn, ctx, run_id, exc, adapter="cdc_object_indexer")
            raise

    def _cdc_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
    ) -> ObjectTypeRow:
        object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
        cdc_source_dataset(object_type)
        return object_type

    def _create_cdc_index_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        events: Sequence[ObjectPropertyMap],
    ) -> str:
        run_id = _new_id("index_run")
        self.object_index_repository.create_index_run(
            transaction=conn,
            record=self._cdc_index_run_record(ctx, object_type, events, run_id),
        )
        return run_id

    def _cdc_index_run_record(
        self,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        events: Sequence[ObjectPropertyMap],
        run_id: str,
    ) -> IndexRunRecord:
        now = _now()
        return IndexRunRecord(
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type["id"],
            object_type_api_name=object_type["api_name"],
            trigger_type="cdc_incremental",
            source_ref={"cdc_dataset": cdc_source_dataset(object_type), "event_count": len(events)},
            status="running",
            cursor={},
            rows_read=0,
            objects_upserted=0,
            objects_deleted=0,
            links_upserted=0,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
        )

    def _parse_cdc_events(
        self,
        conn: TransactionContext,
        object_type: ObjectTypeRow,
        events: Sequence[ObjectPropertyMap],
    ) -> tuple[ObjectCdcEvent, ...]:
        properties = self.ontology_service._properties_for_object_type(conn, object_type["id"])
        return tuple(parse_object_cdc_event(event, object_type, properties) for event in events)

    def _persist_cdc_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        events: Sequence[ObjectCdcEvent],
    ) -> ObjectIndexCdcCounts:
        upserts = deletes = skipped = 0
        for event in events:
            outcome = self._apply_cdc_event(conn, ctx, object_type, event)
            upserts += 1 if outcome == "upserted" else 0
            deletes += 1 if outcome == "deleted" else 0
            skipped += 1 if outcome == "skipped" else 0
        return ObjectIndexCdcCounts(len(events), upserts, deletes, skipped, 0)

    def _apply_cdc_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        event: ObjectCdcEvent,
    ) -> str:
        existing = self.object_records_service._object_record(conn, ctx, object_type["api_name"], event.object_id)
        if existing is not None and cdc_event_should_skip(existing, event):
            return "skipped"
        if event.op == "d":
            return "deleted" if self._apply_cdc_delete(conn, ctx, object_type, event, existing) else "skipped"
        if not self._apply_cdc_upsert(conn, ctx, object_type, event, existing):
            return "skipped"
        return "upserted"

    def _apply_cdc_upsert(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        event: ObjectCdcEvent,
        existing: ObjectRecordRow | None,
    ) -> bool:
        if existing is None:
            self._insert_cdc_object_record(conn, ctx, object_type, event, deleted=False)
            return True
        self._record_conflicts_for_base_update(
            conn,
            ctx,
            object_type,
            event.object_id,
            existing,
            event.base_patch,
            source_dataset_version_id=cdc_source_dataset_version_id(event),
        )
        return self._update_cdc_object_record(conn, ctx, object_type, event, existing, deleted=False)

    def _apply_cdc_delete(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        event: ObjectCdcEvent,
        existing: ObjectRecordRow | None,
    ) -> bool:
        if existing is None:
            self._insert_cdc_object_record(conn, ctx, object_type, event, deleted=True)
            return True
        return self._update_cdc_object_record(conn, ctx, object_type, event, existing, deleted=True)

    def _insert_cdc_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        event: ObjectCdcEvent,
        *,
        deleted: bool,
    ) -> None:
        base_patch = dict(event.base_patch)
        current = dict(self._merge_properties(conn, object_type["id"], base_patch, {}))
        index_version = self.object_index_repository.active_index_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type["id"],
        )
        now = _now()
        self.object_index_repository.insert_object_record(
            transaction=conn,
            record=_cdc_object_insert(ctx, object_type, event, base_patch, current, index_version, deleted, now),
        )
        self._emit_object_changed(
            conn,
            ctx,
            object_type["api_name"],
            event.object_id,
            cdc_source_dataset_version_id(event),
        )

    def _update_cdc_object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        event: ObjectCdcEvent,
        existing: ObjectRecordRow,
        *,
        deleted: bool,
    ) -> bool:
        base_patch = dict(event.base_patch or existing["base_properties"])
        current = dict(self._merge_properties(conn, object_type["id"], base_patch, existing["edit_properties"]))
        updated = self.object_index_repository.update_object_record_from_cdc(
            transaction=conn,
            record=_build_cdc_object_record_update(existing, ctx, current, base_patch, event, deleted=deleted),
        )
        if not updated:
            refreshed = self.object_records_service._object_record(conn, ctx, object_type["api_name"], event.object_id)
            if refreshed is not None and cdc_event_should_skip(refreshed, event):
                return False
            raise ConflictDetected(
                "CDC object record changed during update",
                details={"objectType": object_type["api_name"], "objectId": event.object_id},
            )
        self._emit_object_changed(
            conn,
            ctx,
            object_type["api_name"],
            event.object_id,
            cdc_source_dataset_version_id(event),
        )
        return True

    def _audit_cdc_index(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
        run_id: str,
        counts: ObjectIndexCdcCounts,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="object.index.cdc_applied",
            resource_type="object_type",
            resource_id=object_type["id"],
            action="index_cdc_events",
            after_ref={
                "index_run_id": run_id,
                "objects_upserted": counts.objects_upserted,
                "objects_deleted": counts.objects_deleted,
                "events_skipped": counts.events_skipped,
            },
        )


def _cdc_deletion_reason(deleted: bool) -> str | None:
    return "source_deleted" if deleted else None


def _cdc_object_insert(
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    event: ObjectCdcEvent,
    base_patch: ObjectPropertyMap,
    current: ObjectPropertyMap,
    index_version: str,
    deleted: bool,
    now: str,
) -> ObjectRecordInsert:
    return ObjectRecordInsert(
        record_id=_new_id("obj"),
        tenant_id=ctx.tenant_id,
        object_type_id=object_type["id"],
        object_type_api_name=object_type["api_name"],
        object_id=event.object_id,
        properties=current,
        base_properties=base_patch,
        edit_properties={},
        property_versions=cdc_property_versions(None, event, current),
        source_dataset_version_id=cdc_source_dataset_version_id(event),
        source_hash=cdc_source_hash(event, base_patch),
        object_version=1,
        deleted=deleted,
        deletion_reason=_cdc_deletion_reason(deleted),
        created_at=now,
        updated_at=now,
        index_version=index_version,
        is_active=True,
    )


def _build_cdc_object_record_update(
    existing: ObjectRecordRow,
    ctx: RequestContext,
    current: dict[str, object],
    base_patch: dict[str, object],
    event: ObjectCdcEvent,
    *,
    deleted: bool,
) -> ObjectRecordCdcUpdate:
    """Build the ObjectRecordCdcUpdate record for a CDC object update."""
    return ObjectRecordCdcUpdate(
        record_id=existing["id"],
        tenant_id=ctx.tenant_id,
        expected_object_version=existing["object_version"],
        properties=current,
        base_properties=base_patch,
        property_versions=cdc_property_versions(existing, event, current),
        source_dataset_version_id=cdc_source_dataset_version_id(event),
        source_hash=cdc_source_hash(event, base_patch),
        object_version=existing["object_version"] + 1,
        deleted=deleted,
        deletion_reason=_cdc_deletion_reason(deleted),
        updated_at=_now(),
    )
