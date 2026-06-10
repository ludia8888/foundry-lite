from __future__ import annotations

from typing import Any

from foundry_lite.application.ports import (
    IndexRunRecord,
    ObjectConflictRecord,
    ObjectLinkInsert,
    ObjectRecordInsert,
    ObjectRecordSourceUpdate,
)
from foundry_lite.application.primitives import (
    _json_hash,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ValidationFailed,
)


class ObjectIndexingService(CoreService):
    required_dependencies = ("engine", "compute_adapter", "object_index_repository")

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
            backing = object_type["backing"]
            dataset = self.dataset_registry_service.get_dataset(backing["dataset"], ctx=ctx)
            version = self.dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
            run_id = _new_id("index_run")
            now = _now()
            self.object_index_repository.create_index_run(
                transaction=conn,
                record=IndexRunRecord(
                    run_id=run_id,
                    tenant_id=ctx.tenant_id,
                    object_type_id=object_type["id"],
                    object_type_api_name=object_type_api_name,
                    trigger_type="reindex",
                    source_ref={"dataset_version_id": version["id"]},
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
                ),
            )

        rows = self.compute_adapter.rows_from_parquet(self.dataset_transaction_service._version_file_path(version))
        objects_upserted = 0
        links_upserted = 0
        try:
            with self.engine.begin() as conn:
                for row in rows:
                    self._index_object_row(conn, ctx, object_type, row, version["id"])
                    objects_upserted += 1
                links_upserted = self._index_links_for_object_type(
                    conn,
                    ctx,
                    object_type,
                    rows,
                    version["id"],
                )
                self.object_index_repository.mark_index_run_succeeded(
                    transaction=conn,
                    run_id=run_id,
                    rows_read=len(rows),
                    objects_upserted=objects_upserted,
                    links_upserted=links_upserted,
                    cursor={"last_row": len(rows)},
                    completed_at=_now(),
                )
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="object.index.rebuilt",
                    resource_type="object_type",
                    resource_id=object_type["id"],
                    action="index_rebuild",
                    after_ref={"objects_upserted": objects_upserted},
                )
            return {
                "index_run_id": run_id,
                "object_type": object_type_api_name,
                "rows_read": len(rows),
                "objects_upserted": objects_upserted,
                "links_upserted": links_upserted,
            }
        except Exception as exc:
            with self.engine.begin() as conn:
                self.object_index_repository.mark_index_run_failed(
                    transaction=conn,
                    run_id=run_id,
                    error=self.runtime_service._error_payload(exc),
                    completed_at=_now(),
                )
            raise

    def _index_object_row(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type: dict[str, Any],
        row: dict[str, Any],
        source_dataset_version_id: str,
    ) -> None:
        properties = self.ontology_service._properties_for_object_type(conn, object_type["id"])
        pk_prop = next(prop for prop in properties if prop["api_name"] == object_type["primary_key_property"])
        object_id = row.get(pk_prop["column_name"])
        if object_id in {None, ""}:
            raise ValidationFailed("object primary key cannot be null")
        base_patch = {}
        for prop in properties:
            if prop["source"] == "dataset":
                base_patch[prop["api_name"]] = row.get(prop["column_name"])
        existing = self.object_records_service._object_record(conn, ctx, object_type["api_name"], str(object_id))
        now = _now()
        if existing is None:
            current = self._merge_properties(conn, object_type["id"], base_patch, {})
            self.object_index_repository.insert_object_record(
                transaction=conn,
                record=ObjectRecordInsert(
                    record_id=_new_id("obj"),
                    tenant_id=ctx.tenant_id,
                    object_type_id=object_type["id"],
                    object_type_api_name=object_type["api_name"],
                    object_id=str(object_id),
                    properties=current,
                    base_properties=base_patch,
                    edit_properties={},
                    property_versions={key: 1 for key in current},
                    source_dataset_version_id=source_dataset_version_id,
                    source_hash=_json_hash(base_patch),
                    object_version=1,
                    deleted=False,
                    deletion_reason=None,
                    created_at=now,
                    updated_at=now,
                ),
            )
        else:
            self._record_conflicts_for_base_update(
                conn,
                ctx,
                object_type,
                str(object_id),
                existing,
                base_patch,
                source_dataset_version_id,
            )
            current = self._merge_properties(conn, object_type["id"], base_patch, existing["edit_properties"])
            self.object_index_repository.update_object_record_from_source(
                transaction=conn,
                record=ObjectRecordSourceUpdate(
                    record_id=existing["id"],
                    properties=current,
                    base_properties=base_patch,
                    source_dataset_version_id=source_dataset_version_id,
                    source_hash=_json_hash(base_patch),
                    object_version=existing["object_version"] + 1,
                    updated_at=now,
                ),
            )
        self.runtime_service._outbox(
            conn,
            ctx,
            "object.changed",
            "object",
            f"{object_type['api_name']}/{object_id}",
            {"objectType": object_type["api_name"], "objectId": str(object_id)},
            idempotency_key=f"{source_dataset_version_id}:{object_type['api_name']}:{object_id}",
            correlation_id=source_dataset_version_id,
        )

    def _record_conflicts_for_base_update(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type: dict[str, Any],
        object_id: str,
        existing: dict[str, Any],
        base_patch: dict[str, Any],
        source_dataset_version_id: str,
    ) -> None:
        for prop in self.ontology_service._properties_for_object_type(conn, object_type["id"]):
            if prop["edit_policy"] != "conflict_requires_review":
                continue
            api_name = prop["api_name"]
            has_conflicting_edit = api_name in existing["edit_properties"] and existing["edit_properties"][
                api_name
            ] != base_patch.get(api_name)
            if has_conflicting_edit:
                self.object_index_repository.insert_object_conflict(
                    transaction=conn,
                    record=ObjectConflictRecord(
                        conflict_id=_new_id("conflict"),
                        tenant_id=ctx.tenant_id,
                        object_type_id=object_type["id"],
                        object_id=object_id,
                        property_api_name=api_name,
                        source_value=base_patch.get(api_name),
                        edit_value=existing["edit_properties"][api_name],
                        source_dataset_version_id=source_dataset_version_id,
                        edit_id=None,
                        status="open",
                        created_at=_now(),
                    ),
                )

    def _merge_properties(
        self,
        conn: Any,
        object_type_id: str,
        base: dict[str, Any],
        edits: dict[str, Any],
    ) -> dict[str, Any]:
        current: dict[str, Any] = {}
        for prop in self.ontology_service._properties_for_object_type(conn, object_type_id):
            name = prop["api_name"]
            policy = prop["edit_policy"]
            if policy == "edit_only":
                if name in edits:
                    current[name] = edits[name]
            elif policy in {"edit_wins", "conflict_requires_review"}:
                current[name] = edits[name] if name in edits else base.get(name)
            elif policy == "source_wins":
                current[name] = base[name] if name in base else edits.get(name)
            else:
                current[name] = base.get(name)
        return current

    def _index_links_for_object_type(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type: dict[str, Any],
        rows: list[dict[str, Any]],
        source_dataset_version_id: str,
    ) -> int:
        active = self.ontology_service._active_ontology_version(conn, ctx)
        links = self.object_index_repository.link_types_for_object_type(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            from_object_type_id=object_type["id"],
        )
        count = 0
        for link in links:
            from_key = link["backing"]["fromKey"]
            to_key = link["backing"]["toKey"]
            for row in rows:
                from_id = row.get(from_key)
                to_id = row.get(to_key)
                if from_id in {None, ""} or to_id in {None, ""}:
                    continue
                existing = self.object_index_repository.object_link(
                    transaction=conn,
                    tenant_id=ctx.tenant_id,
                    link_type_id=link["id"],
                    from_object_id=str(from_id),
                    to_object_id=str(to_id),
                )
                if existing:
                    self.object_index_repository.refresh_object_link(
                        transaction=conn,
                        link_id=existing["id"],
                        link_version=existing["link_version"] + 1,
                        source_dataset_version_id=source_dataset_version_id,
                        updated_at=_now(),
                    )
                else:
                    self.object_index_repository.insert_object_link(
                        transaction=conn,
                        record=ObjectLinkInsert(
                            link_id=_new_id("olink"),
                            tenant_id=ctx.tenant_id,
                            link_type_id=link["id"],
                            link_type_api_name=link["api_name"],
                            from_object_type_id=link["from_object_type_id"],
                            from_api_name=link["from_api_name"],
                            from_object_id=str(from_id),
                            to_object_type_id=link["to_object_type_id"],
                            to_api_name=link["to_api_name"],
                            to_object_id=str(to_id),
                            properties={},
                            source_dataset_version_id=source_dataset_version_id,
                            link_version=1,
                            deleted=False,
                            deletion_reason=None,
                            updated_at=_now(),
                        ),
                    )
                count += 1
        return count
