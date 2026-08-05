from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Barrier
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.action_log_types import (
    ActionLogEntryRecord,
    ActionLogEntryRow,
    ActionLogObjectRecord,
    ActionLogObjectRow,
    ObjectRestoreWrite,
)
from foundry_lite.application.ports.action_repository import (
    ActionRepository,
    ActionRunRecord,
    ActionRunRow,
    ActionRunUsageRow,
    ActionWritebackReconciliation,
    ActionWritebackRecord,
    ObjectCreateWrite,
    ObjectDeleteWrite,
    ObjectEditRecord,
    ObjectEditRow,
    ObjectLinkDeleteWrite,
    ObjectLinkWrite,
    ObjectTargetUpdate,
)
from foundry_lite.application.ports.object_read_repository import (
    ObjectAggregationGroup,
    ObjectAggregationMetric,
    ObjectOrderBy,
    ObjectQueryCursor,
    ObjectQueryItem,
)
from foundry_lite.application.ports.transaction_context import (
    ACTION_RUN_COMPENSATION_REQUIRED,
    ACTION_RUN_FAILED,
    ACTION_RUN_OUTCOME_UNKNOWN,
    ACTION_RUN_RECONCILED,
    ACTION_RUN_RETRYABLE,
    ACTION_RUN_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.application.services.action_log_ontology_aggregation import aggregate_action_log_items
from foundry_lite.application.services.action_log_ontology_query import filtered_sorted_logs
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyActionRepository
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class ActionHarness(Protocol):
    repository: ActionRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_object_record(self, **kwargs: Any) -> None: ...

    def action_run_rows(self) -> list[dict[str, Any]]: ...

    def writeback_rows(self) -> list[dict[str, Any]]: ...

    def object_rows(self) -> list[dict[str, Any]]: ...

    def object_link_rows(self) -> list[dict[str, Any]]: ...

    def object_record_version_rows(self) -> list[dict[str, Any]]: ...

    def object_edit_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeActionRepository:
    action_runs: list[ActionRunRow] = field(default_factory=list)
    action_writebacks: list[dict[str, Any]] = field(default_factory=list)
    object_records: list[dict[str, Any]] = field(default_factory=list)
    object_links: list[dict[str, Any]] = field(default_factory=list)
    object_edits: list[dict[str, Any]] = field(default_factory=list)
    action_logs: list[dict[str, Any]] = field(default_factory=list)
    action_log_object_rows: list[dict[str, Any]] = field(default_factory=list)

    def action_run_by_idempotency(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> ActionRunRow | None:
        del transaction
        for row in self.action_runs:
            if (
                row["tenant_id"] == tenant_id
                and row["action_type_id"] == action_type_id
                and row["actor_user_id"] == actor_user_id
                and row["idempotency_key"] == idempotency_key
            ):
                return row.copy()
        return None

    def action_run_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
    ) -> ActionRunRow | None:
        del transaction
        for row in self.action_runs:
            if row["tenant_id"] == tenant_id and row["id"] == action_run_id:
                return row.copy()
        return None

    def insert_action_run(self, *, transaction: Any, record: ActionRunRecord) -> None:
        del transaction
        self.action_runs.append(_action_run_row(record))

    def insert_action_run_or_get_existing(self, *, transaction: Any, record: ActionRunRecord) -> ActionRunRow | None:
        existing = self.action_run_by_idempotency(
            transaction=transaction,
            tenant_id=record.tenant_id,
            action_type_id=record.action_type_id,
            actor_user_id=record.actor_user_id,
            idempotency_key=record.idempotency_key,
        )
        if existing is not None:
            return existing
        self.action_runs.append(_action_run_row(record))
        return None

    def list_action_runs_by_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        statuses: Any,
        limit: int,
    ) -> list[ActionRunRow]:
        del transaction
        matches = [
            row.copy() for row in self.action_runs if row["tenant_id"] == tenant_id and row["status"] in tuple(statuses)
        ]
        return matches[:limit]

    def action_runs_for_monitoring(
        self, *, transaction: Any, tenant_id: str, created_at_from: str, limit: int
    ) -> list[ActionRunRow]:
        del transaction
        rows = [row.copy() for row in self.action_runs if row["tenant_id"] == tenant_id]
        recent = [row for row in rows if row["created_at"] >= created_at_from]
        return sorted(recent, key=lambda row: (row["created_at"], row["id"]), reverse=True)[:limit]

    def update_action_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
        transition: StatusTransition,
        error: Mapping[str, object] | None,
        completed_at: str | None,
        result: Mapping[str, object] | None = None,
    ) -> bool:
        del transaction
        for row in self.action_runs:
            if (
                row["tenant_id"] == tenant_id
                and row["id"] == action_run_id
                and row["status"] in transition.from_statuses
            ):
                row.update(status=transition.to_status, error=error, result=result, completed_at=completed_at)
                return True
        return False

    def update_action_run_parameters(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
        parameters: Mapping[str, object],
    ) -> bool:
        del transaction
        for row in self.action_runs:
            if row["tenant_id"] == tenant_id and row["id"] == action_run_id and row["status"] == "received":
                row["parameters"] = dict(parameters)
                return True
        return False

    def action_run_usage(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        since: str,
        action_type_api_name: str | None = None,
        target_object_type_api_name: str | None = None,
    ) -> ActionRunUsageRow:
        del transaction
        rows = [
            row
            for row in self.action_runs
            if row["tenant_id"] == tenant_id
            and row["created_at"] >= since
            and (action_type_api_name is None or row["action_type_api_name"] == action_type_api_name)
            and (
                target_object_type_api_name is None or row["target_object_type_api_name"] == target_object_type_api_name
            )
        ]
        status_counts: dict[str, int] = {}
        for row in rows:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        return {
            "status_counts": status_counts,
            "total_runs": len(rows),
            "distinct_actor_count": len({row["actor_user_id"] for row in rows}),
            "last_run_at": max((row["created_at"] for row in rows), default=None),
        }

    def insert_action_writeback(self, *, transaction: Any, record: ActionWritebackRecord) -> None:
        del transaction
        self.action_writebacks.append(_writeback_row(record))

    def action_writeback_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        writeback_id: str,
    ) -> ActionWritebackRecord | None:
        del transaction
        for row in self.action_writebacks:
            if row["tenant_id"] == tenant_id and row["id"] == writeback_id:
                return _writeback_record_from_row(row)
        return None

    def list_action_writebacks(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        statuses: Sequence[str],
        limit: int,
    ) -> list[ActionWritebackRecord]:
        del transaction
        status_set = set(statuses)
        rows = [
            _writeback_record_from_row(row)
            for row in self.action_writebacks
            if row["tenant_id"] == tenant_id and row["status"] in status_set
        ]
        return sorted(rows, key=lambda row: (row.created_at, row.writeback_id), reverse=True)[:limit]

    def reconcile_action_writeback(self, *, transaction: Any, record: ActionWritebackReconciliation) -> bool:
        del transaction
        for row in self.action_writebacks:
            if (
                row["tenant_id"] == record.tenant_id
                and row["id"] == record.writeback_id
                and row["action_run_id"] == record.action_run_id
                and row["status"] in {"outcome_unknown", "compensation_required"}
            ):
                row.update(status="reconciled", response=record.response, completed_at=record.completed_at)
                return True
        return False

    def update_object_target(self, *, transaction: Any, record: ObjectTargetUpdate) -> bool:
        del transaction
        for row in self.object_records:
            if (
                row["tenant_id"] == record.tenant_id
                and row["id"] == record.object_record_id
                and row["object_version"] == record.expected_object_version
            ):
                row.update(
                    edit_properties=record.edit_properties,
                    properties=record.properties,
                    object_version=record.next_object_version,
                    updated_at=record.updated_at,
                )
                return True
        return False

    def create_object_record(self, *, transaction: Any, record: ObjectCreateWrite) -> bool:
        del transaction
        identity = (record.tenant_id, record.object_type_id, record.object_id, "active")
        for row in self.object_records:
            if (row["tenant_id"], row["object_type_id"], row["object_id"], row["index_version"]) == identity:
                return False
        self.object_records.append(_created_object_row(record))
        return True

    def soft_delete_object_target(self, *, transaction: Any, record: ObjectDeleteWrite) -> bool:
        del transaction
        for row in self.object_records:
            if (
                row["tenant_id"] == record.tenant_id
                and row["id"] == record.object_record_id
                and row["object_version"] == record.expected_object_version
            ):
                row.update(
                    deleted=True,
                    is_active=False,
                    deletion_reason=record.deletion_reason,
                    object_version=record.expected_object_version + 1,
                    updated_at=record.updated_at,
                )
                return True
        return False

    def create_object_link(self, *, transaction: Any, record: ObjectLinkWrite) -> None:
        del transaction
        identity = (record.tenant_id, record.link_type_id, record.from_object_id, record.to_object_id, "active")
        for row in self.object_links:
            key = (
                row["tenant_id"],
                row["link_type_id"],
                row["from_object_id"],
                row["to_object_id"],
                row["index_version"],
            )
            if key == identity:
                row.update(
                    is_active=True,
                    deleted=False,
                    deletion_reason=None,
                    link_version=row["link_version"] + 1,
                    updated_at=record.updated_at,
                )
                return
        self.object_links.append(_object_link_row(record))

    def soft_delete_object_link(self, *, transaction: Any, record: ObjectLinkDeleteWrite) -> bool:
        del transaction
        deleted_any = False
        for row in self.object_links:
            if (
                row["tenant_id"] == record.tenant_id
                and row["link_type_id"] == record.link_type_id
                and row["from_object_id"] == record.from_object_id
                and row["to_object_id"] == record.to_object_id
                and row["is_active"]
                and not row["deleted"]
            ):
                row.update(
                    is_active=False,
                    deleted=True,
                    deletion_reason=record.deletion_reason,
                    link_version=row["link_version"] + 1,
                    updated_at=record.updated_at,
                )
                deleted_any = True
        return deleted_any

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        del transaction
        self.object_edits.append(_object_edit_row(record))

    def object_edits_for_run(self, *, transaction: Any, tenant_id: str, action_run_id: str) -> list[ObjectEditRow]:
        del transaction
        rows = [
            row for row in self.object_edits if row["tenant_id"] == tenant_id and row["action_run_id"] == action_run_id
        ]
        return [cast(ObjectEditRow, row.copy()) for row in sorted(rows, key=lambda row: (row["created_at"], row["id"]))]

    def latest_object_edit(
        self, *, transaction: Any, tenant_id: str, object_type_id: str, object_id: str
    ) -> ObjectEditRow | None:
        del transaction
        rows = [
            row
            for row in self.object_edits
            if row["tenant_id"] == tenant_id
            and row["object_type_id"] == object_type_id
            and row["object_id"] == object_id
        ]
        if not rows:
            return None
        return cast(ObjectEditRow, max(rows, key=lambda row: (row["created_at"], row["id"])).copy())

    def insert_action_log(
        self,
        *,
        transaction: Any,
        entry: ActionLogEntryRecord,
        objects: Sequence[ActionLogObjectRecord],
    ) -> ActionLogEntryRow | None:
        del transaction
        if any(
            row["tenant_id"] == entry.tenant_id and row["action_run_id"] == entry.action_run_id
            for row in self.action_logs
        ):
            return None
        row = _action_log_entry_row(entry)
        self.action_logs.append(row)
        self.action_log_object_rows.extend(_action_log_object_row(item) for item in objects)
        return cast(ActionLogEntryRow, row.copy())

    def action_log_by_run_id(self, *, transaction: Any, tenant_id: str, action_run_id: str) -> ActionLogEntryRow | None:
        del transaction
        for row in self.action_logs:
            if row["tenant_id"] == tenant_id and row["action_run_id"] == action_run_id:
                return cast(ActionLogEntryRow, row.copy())
        return None

    def action_log_objects(
        self, *, transaction: Any, tenant_id: str, action_log_entry_id: str
    ) -> list[ActionLogObjectRow]:
        del transaction
        rows = [
            row
            for row in self.action_log_object_rows
            if row["tenant_id"] == tenant_id and row["action_log_entry_id"] == action_log_entry_id
        ]
        return [cast(ActionLogObjectRow, row.copy()) for row in sorted(rows, key=lambda row: row["ordinal"])]

    def list_action_logs(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        before_created_at: str | None,
        before_log_id: str | None,
        limit: int,
        action_type_api_name: str | None = None,
    ) -> list[ActionLogEntryRow]:
        del transaction
        rows = [row for row in self.action_logs if row["tenant_id"] == tenant_id]
        if action_type_api_name is not None:
            rows = [row for row in rows if row["action_type_api_name"] == action_type_api_name]
        if before_created_at is not None and before_log_id is not None:
            rows = [row for row in rows if (row["created_at"], row["id"]) < (before_created_at, before_log_id)]
        ordered = sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)[:limit]
        return [cast(ActionLogEntryRow, row.copy()) for row in ordered]

    def query_action_logs(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_api_name: str,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[ObjectOrderBy],
        cursor: ObjectQueryCursor | None,
        search_text: str | None,
        limit: int,
    ) -> list[ActionLogEntryRow]:
        del transaction
        rows = [
            row
            for row in self.action_logs
            if row["tenant_id"] == tenant_id and row["action_type_api_name"] == action_type_api_name
        ]
        items = [_fake_log_query_item(row, self.action_log_object_rows) for row in rows]
        visible = filtered_sorted_logs(items, filter_ast, order_by, search_text)
        start = _fake_log_cursor_start(visible, order_by, cursor)
        by_run_id = {str(row["action_run_id"]): row for row in rows}
        return [cast(ActionLogEntryRow, by_run_id[item["objectId"]].copy()) for item in visible[start : start + limit]]

    def aggregate_action_logs(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_api_name: str,
        filter_ast: Mapping[str, object] | None,
        group_by: Sequence[str],
        metrics: Sequence[ObjectAggregationMetric],
        group_limit: int,
    ) -> list[ObjectAggregationGroup]:
        del transaction
        items = [
            _fake_log_query_item(row, self.action_log_object_rows)
            for row in self.action_logs
            if row["tenant_id"] == tenant_id and row["action_type_api_name"] == action_type_api_name
        ]
        visible = filtered_sorted_logs(items, filter_ast, [], None)
        result = aggregate_action_log_items("[LOG] test", visible, group_by, metrics)
        return result["groups"][:group_limit]

    def mark_action_log_reverted(
        self, *, transaction: Any, tenant_id: str, action_run_id: str, reverted_by_run_id: str
    ) -> bool:
        del transaction
        for row in self.action_logs:
            if (
                row["tenant_id"] == tenant_id
                and row["action_run_id"] == action_run_id
                and row["revert_status"] == "eligible"
                and row["reverted_by_run_id"] is None
            ):
                row.update(revert_status="reverted", reverted_by_run_id=reverted_by_run_id)
                return True
        return False

    def object_target_for_revert(
        self, *, transaction: Any, tenant_id: str, object_type_id: str, object_id: str
    ) -> Any | None:
        del transaction
        for row in self.object_records:
            if (
                row["tenant_id"] == tenant_id
                and row["object_type_id"] == object_type_id
                and row["object_id"] == object_id
            ):
                return row.copy()
        return None

    def object_link_for_revert(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_id: str,
        from_object_id: str,
        to_object_id: str,
    ) -> Any | None:
        del transaction
        for row in self.object_links:
            if (
                row["tenant_id"] == tenant_id
                and row["link_type_id"] == link_type_id
                and row["from_object_id"] == from_object_id
                and row["to_object_id"] == to_object_id
            ):
                return row.copy()
        return None

    def restore_object_target(self, *, transaction: Any, record: ObjectRestoreWrite) -> bool:
        del transaction
        for row in self.object_records:
            if (
                row["tenant_id"] == record.tenant_id
                and row["id"] == record.object_record_id
                and row["object_version"] == record.expected_object_version
                and row["deleted"]
            ):
                row.update(
                    deleted=False,
                    is_active=True,
                    deletion_reason=None,
                    object_version=record.expected_object_version + 1,
                    updated_at=record.updated_at,
                )
                return True
        return False


@dataclass
class FakeActionHarness:
    repository: ActionRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_object_record(self, **kwargs: Any) -> None:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        repository.object_records.append(_object_record_row(**kwargs))

    def action_run_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.action_runs]

    def writeback_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.action_writebacks]

    def object_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.object_records]

    def object_link_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.object_links]

    def object_record_version_rows(self) -> list[dict[str, Any]]:
        # The fake models current-state rows only; version history is asserted against real engines.
        return []

    def object_edit_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.object_edits]


@dataclass
class SqlAlchemyActionHarness:
    repository: ActionRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def add_object_record(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_records).values(**_object_record_row(**kwargs)))

    def action_run_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.action_runs)

    def writeback_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.action_writebacks)

    def object_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_records)

    def object_link_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_links)

    def object_record_version_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_record_versions)

    def object_edit_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_edits)

    def _rows(self, table: Any) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(table)).mappings().all()
        return [dict(row) for row in rows]


def _action_run_record(
    run_id: str = "arun_1",
    *,
    tenant_id: str = "tenant-demo",
    action_type_id: str = "atype_approve",
    action_type_api_name: str = "approveOrder",
    actor_user_id: str = "actor-demo",
    target_object_type_api_name: str = "Order",
    status: str = "received",
    idempotency_key: str = "idem-1",
    request_fingerprint: str = "fingerprint-1",
    created_at: str = "2026-06-10T00:00:00Z",
) -> ActionRunRecord:
    return ActionRunRecord(
        action_run_id=run_id,
        tenant_id=tenant_id,
        action_type_id=action_type_id,
        action_type_api_name=action_type_api_name,
        actor_user_id=actor_user_id,
        target_object_type_id="ot_order",
        target_object_type_api_name=target_object_type_api_name,
        target_object_id="O-1",
        expected_object_version=1,
        parameters={"status": "APPROVED"},
        status=status,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        result=None,
        error=None,
        created_at=created_at,
        completed_at=None,
    )


def _action_run_row(record: ActionRunRecord) -> ActionRunRow:
    return {
        "id": record.action_run_id,
        "tenant_id": record.tenant_id,
        "action_type_id": record.action_type_id,
        "action_type_api_name": record.action_type_api_name,
        "actor_user_id": record.actor_user_id,
        "target_object_type_id": record.target_object_type_id,
        "target_object_type_api_name": record.target_object_type_api_name,
        "target_object_id": record.target_object_id,
        "expected_object_version": record.expected_object_version,
        "parameters": record.parameters,
        "status": record.status,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "result": record.result,
        "error": record.error,
        "external_writeback_uri": record.external_writeback_uri,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def _writeback_record(
    writeback_id: str = "wb_1",
    *,
    tenant_id: str = "tenant-demo",
    action_run_id: str = "arun_1",
    status: str = "succeeded",
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    created_at: str = "2026-06-10T00:00:01Z",
) -> ActionWritebackRecord:
    return ActionWritebackRecord(
        writeback_id=writeback_id,
        tenant_id=tenant_id,
        action_run_id=action_run_id,
        mode="before_commit",
        connector_id="mock_erp",
        request=request or {"connector": "mock_erp", "simulated": True},
        response=response or {"status_code": 200, "simulated": True},
        status=status,
        idempotency_key="idem-1",
        attempts=1,
        created_at=created_at,
        completed_at="2026-06-10T00:00:02Z",
    )


def _writeback_row(record: ActionWritebackRecord) -> dict[str, Any]:
    return {
        "id": record.writeback_id,
        "tenant_id": record.tenant_id,
        "action_run_id": record.action_run_id,
        "mode": record.mode,
        "connector_id": record.connector_id,
        "request": record.request,
        "response": record.response,
        "status": record.status,
        "idempotency_key": record.idempotency_key,
        "attempts": record.attempts,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def _writeback_record_from_row(row: Mapping[str, Any]) -> ActionWritebackRecord:
    return ActionWritebackRecord(
        writeback_id=row["id"],
        tenant_id=row["tenant_id"],
        action_run_id=row["action_run_id"],
        mode=row["mode"],
        connector_id=row["connector_id"],
        request=row["request"],
        response=row["response"],
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _object_record_row(
    *,
    record_id: str = "obj_order_1",
    tenant_id: str = "tenant-demo",
    object_version: int = 3,
) -> dict[str, Any]:
    return {
        "id": record_id,
        "tenant_id": tenant_id,
        "object_type_id": "ot_order",
        "object_type_api_name": "Order",
        "object_id": "O-1",
        "index_version": "active",
        "is_active": True,
        "properties": {"status": "PENDING"},
        "base_properties": {"status": "PENDING"},
        "edit_properties": {},
        "property_versions": {"status": 1},
        "source_dataset_version_id": "dsv_orders_1",
        "source_hash": "hash-demo",
        "object_version": object_version,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


def _object_target_update(*, expected_object_version: int = 3) -> ObjectTargetUpdate:
    return ObjectTargetUpdate(
        object_record_id="obj_order_1",
        tenant_id="tenant-demo",
        expected_object_version=expected_object_version,
        edit_properties={"status": "APPROVED"},
        properties={"status": "APPROVED"},
        next_object_version=expected_object_version + 1,
        updated_at="2026-06-10T00:00:03Z",
    )


def _object_create_write(
    *,
    object_record_id: str = "obj_ship_1",
    object_id: str = "S-1",
    properties: dict[str, Any] | None = None,
) -> ObjectCreateWrite:
    return ObjectCreateWrite(
        object_record_id=object_record_id,
        tenant_id="tenant-demo",
        object_type_id="ot_shipment",
        object_type_api_name="Shipment",
        object_id=object_id,
        properties=properties or {"carrier": "UPS"},
        created_at="2026-06-10T00:00:05Z",
    )


def _created_object_row(record: ObjectCreateWrite) -> dict[str, Any]:
    properties = dict(record.properties)
    return {
        "id": record.object_record_id,
        "tenant_id": record.tenant_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "index_version": "active",
        "is_active": True,
        "properties": properties,
        "base_properties": {},
        "edit_properties": dict(properties),
        "property_versions": {name: 1 for name in properties},
        "source_dataset_version_id": None,
        "source_hash": None,
        "object_version": 1,
        "object_change_sequence": None,
        "deleted": False,
        "deletion_reason": None,
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }


def _object_delete_write(
    *, object_record_id: str = "obj_order_1", expected_object_version: int = 3
) -> ObjectDeleteWrite:
    return ObjectDeleteWrite(
        object_record_id=object_record_id,
        tenant_id="tenant-demo",
        expected_object_version=expected_object_version,
        deletion_reason="action:FulfillOrder",
        updated_at="2026-06-10T00:00:06Z",
    )


def _object_link_write(*, link_record_id: str = "lnk_1") -> ObjectLinkWrite:
    return ObjectLinkWrite(
        link_record_id=link_record_id,
        tenant_id="tenant-demo",
        link_type_id="lt_order_shipment",
        link_type_api_name="OrderShipment",
        from_object_type_id="ot_order",
        from_api_name="Order",
        from_object_id="O-1",
        to_object_type_id="ot_shipment",
        to_api_name="Shipment",
        to_object_id="S-1",
        updated_at="2026-06-10T00:00:07Z",
    )


def _object_link_row(record: ObjectLinkWrite) -> dict[str, Any]:
    return {
        "id": record.link_record_id,
        "tenant_id": record.tenant_id,
        "link_type_id": record.link_type_id,
        "link_type_api_name": record.link_type_api_name,
        "index_version": "active",
        "is_active": True,
        "from_object_type_id": record.from_object_type_id,
        "from_api_name": record.from_api_name,
        "from_object_id": record.from_object_id,
        "to_object_type_id": record.to_object_type_id,
        "to_api_name": record.to_api_name,
        "to_object_id": record.to_object_id,
        "properties": {},
        "source_dataset_version_id": None,
        "link_version": 1,
        "deleted": False,
        "deletion_reason": None,
        "updated_at": record.updated_at,
    }


def _object_link_delete_write() -> ObjectLinkDeleteWrite:
    return ObjectLinkDeleteWrite(
        tenant_id="tenant-demo",
        link_type_id="lt_order_shipment",
        from_object_id="O-1",
        to_object_id="S-1",
        deletion_reason="action:Unlink",
        updated_at="2026-06-10T00:00:08Z",
    )


def _object_edit_record(edit_id: str = "edit_1") -> ObjectEditRecord:
    return ObjectEditRecord(
        edit_id=edit_id,
        tenant_id="tenant-demo",
        action_run_id="arun_1",
        object_type_id="ot_order",
        object_type_api_name="Order",
        object_id="O-1",
        edit_type="set_property",
        patch={"status": "APPROVED"},
        previous_values={"status": "PENDING"},
        actor_user_id="actor-demo",
        idempotency_key="idem-1",
        created_at="2026-06-10T00:00:04Z",
    )


def _object_edit_row(record: ObjectEditRecord) -> dict[str, Any]:
    return {
        "id": record.edit_id,
        "tenant_id": record.tenant_id,
        "action_run_id": record.action_run_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "edit_type": record.edit_type,
        "patch": record.patch,
        "previous_values": record.previous_values,
        "actor_user_id": record.actor_user_id,
        "idempotency_key": record.idempotency_key,
        "created_at": record.created_at,
        "revert_payload": record.revert_payload,
    }


def _action_log_entry_record() -> ActionLogEntryRecord:
    return ActionLogEntryRecord(
        log_entry_id="alog_1",
        tenant_id="tenant-demo",
        action_run_id="arun_1",
        log_object_type_api_name="[LOG] approveOrder",
        log_object_id="arun_1",
        action_type_id="atype_approve",
        action_type_api_name="approveOrder",
        definition_version="sha256:definition",
        actor_user_id="actor-demo",
        status="succeeded",
        parameters={"status": "APPROVED"},
        result={"status": "succeeded"},
        branch_id=None,
        plan_hash="sha256:plan",
        approval_id=None,
        revert_allowed=True,
        created_at="2026-06-10T00:00:00Z",
        completed_at="2026-06-10T00:00:05Z",
    )


def _action_log_object_record() -> ActionLogObjectRecord:
    return ActionLogObjectRecord(
        log_object_link_id="alogobj_1",
        tenant_id="tenant-demo",
        action_log_entry_id="alog_1",
        object_edit_id="edit_1",
        object_type_id="ot_order",
        object_type_api_name="Order",
        object_id="O-1",
        edit_type="set_property",
        ordinal=0,
    )


def _action_log_entry_row(record: ActionLogEntryRecord) -> dict[str, Any]:
    return {
        "id": record.log_entry_id,
        "tenant_id": record.tenant_id,
        "action_run_id": record.action_run_id,
        "log_object_type_api_name": record.log_object_type_api_name,
        "log_object_id": record.log_object_id,
        "action_type_id": record.action_type_id,
        "action_type_api_name": record.action_type_api_name,
        "definition_version": record.definition_version,
        "actor_user_id": record.actor_user_id,
        "status": record.status,
        "parameters": record.parameters,
        "result": record.result,
        "branch_id": record.branch_id,
        "plan_hash": record.plan_hash,
        "approval_id": record.approval_id,
        "revert_allowed": record.revert_allowed,
        "revert_status": "eligible" if record.revert_allowed else "not_allowed",
        "reverted_by_run_id": None,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def _action_log_object_row(record: ActionLogObjectRecord) -> dict[str, Any]:
    return {
        "id": record.log_object_link_id,
        "tenant_id": record.tenant_id,
        "action_log_entry_id": record.action_log_entry_id,
        "object_edit_id": record.object_edit_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "edit_type": record.edit_type,
        "ordinal": record.ordinal,
    }


def _fake_log_query_item(row: Mapping[str, object], object_rows: Sequence[Mapping[str, object]]) -> ObjectQueryItem:
    edited = [item for item in object_rows if item["action_log_entry_id"] == row["id"]]
    properties = {
        "actionRunId": row["action_run_id"],
        "logEntryId": row["id"],
        "definitionVersion": row["definition_version"],
        "actorUserId": row["actor_user_id"],
        "status": row["status"],
        "parameters": row["parameters"],
        "result": row["result"],
        "branchId": row["branch_id"],
        "planHash": row["plan_hash"],
        "approvalId": row["approval_id"],
        "revertAllowed": row["revert_allowed"],
        "revertStatus": row["revert_status"],
        "revertedByRunId": row["reverted_by_run_id"],
        "effectReceiptCount": 0,
        "editedObjectCount": len(edited),
        "editedObjects": edited,
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
    }
    return {
        "objectType": str(row["log_object_type_api_name"]),
        "objectId": str(row["action_run_id"]),
        "objectVersion": 2 if row["reverted_by_run_id"] else 1,
        "properties": properties,
    }


def _fake_log_cursor_start(
    items: Sequence[ObjectQueryItem], order_by: Sequence[ObjectOrderBy], cursor: ObjectQueryCursor | None
) -> int:
    if cursor is None:
        return 0
    for index, item in enumerate(items):
        values = [item["properties"].get(order["property"]) for order in order_by]
        if item["objectId"] == cursor["object_id"] and values == cursor["values"]:
            return index + 1
    return len(items)


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ActionHarness:
    if request.param == "fake":
        return FakeActionHarness(FakeActionRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyActionHarness(SqlAlchemyActionRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyActionHarness(
        SqlAlchemyActionRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_action_repository_contract_inserts_and_replays_idempotent_runs(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        found = harness.repository.action_run_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_id="atype_approve",
            actor_user_id="actor-demo",
            idempotency_key="idem-1",
        )
        found_by_id = harness.repository.action_run_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
        )
        missing_actor = harness.repository.action_run_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_id="atype_approve",
            actor_user_id="other-actor",
            idempotency_key="idem-1",
        )

    assert found is not None
    assert found["id"] == "arun_1"
    assert found_by_id is not None
    assert found_by_id["id"] == "arun_1"
    assert found["parameters"] == {"status": "APPROVED"}
    assert found["request_fingerprint"] == "fingerprint-1"
    assert missing_actor is None


def test_action_repository_contract_lists_external_pending_runs_for_recovery(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(
            transaction=transaction,
            record=replace(
                _action_run_record("arun_pending_1", idempotency_key="idem-p1", status="external_pending"),
                external_writeback_uri="s3://bucket/order/O-1",
            ),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record("arun_pending_2", idempotency_key="idem-p2", status="external_pending"),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record("arun_done", idempotency_key="idem-done", status="succeeded"),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record(
                "arun_other_tenant", tenant_id="tenant-other", idempotency_key="idem-o", status="external_pending"
            ),
        )
        pending = harness.repository.list_action_runs_by_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            statuses=("external_pending",),
            limit=10,
        )
        limited = harness.repository.list_action_runs_by_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            statuses=("external_pending",),
            limit=1,
        )

    # Only tenant-demo external_pending runs are returned (the write-ahead URI round-trips), the succeeded
    # run and the other tenant's run are excluded, and the limit is honored.
    assert {run["id"] for run in pending} == {"arun_pending_1", "arun_pending_2"}
    assert all(run["status"] == "external_pending" for run in pending)
    uris = {run["id"]: run["external_writeback_uri"] for run in pending}
    assert uris == {"arun_pending_1": "s3://bucket/order/O-1", "arun_pending_2": None}
    assert len(limited) == 1


def test_action_repository_contract_insert_or_get_existing_replays_duplicate(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        first = harness.repository.insert_action_run_or_get_existing(
            transaction=transaction,
            record=_action_run_record(run_id="arun_winner"),
        )
        replay = harness.repository.insert_action_run_or_get_existing(
            transaction=transaction,
            record=_action_run_record(run_id="arun_loser"),
        )

    rows = harness.action_run_rows()
    assert first is None
    assert replay is not None
    assert replay["id"] == "arun_winner"
    assert [row["id"] for row in rows] == ["arun_winner"]


def test_action_repository_contract_replaces_only_received_parameters(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_parameters(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            parameters={"receipt": {"mediaItemVersionId": "miv_committed"}},
        )
        harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_SUCCEEDED,
            error=None,
            completed_at="2026-06-10T00:00:05Z",
        )
        stale = harness.repository.update_action_run_parameters(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            parameters={"receipt": "tampered"},
        )

    assert updated is True
    assert stale is False
    assert harness.action_run_rows()[0]["parameters"] == {"receipt": {"mediaItemVersionId": "miv_committed"}}


def test_action_same_idempotency_key_concurrent_requests_replay_same_action_run(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metadata.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    db.create_database(engine)
    repository = SqlAlchemyActionRepository(engine)
    start = Barrier(2)

    def submit(run_id: str) -> str:
        start.wait()
        with engine.begin() as transaction:
            existing = repository.insert_action_run_or_get_existing(
                transaction=transaction,
                record=_action_run_record(run_id=run_id),
            )
        return existing["id"] if existing is not None else run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["arun_first", "arun_second"]))

    with engine.begin() as transaction:
        rows = transaction.execute(select(db.action_runs)).mappings().all()
    assert len(rows) == 1
    assert results == [rows[0]["id"], rows[0]["id"]]


def test_sqlalchemy_action_run_insert_or_get_existing_rolls_back_with_outer_transaction(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyActionRepository(engine)

    with pytest.raises(RuntimeError):
        with engine.begin() as transaction:
            repository.insert_action_run_or_get_existing(
                transaction=transaction,
                record=_action_run_record(run_id="arun_rollback"),
            )
            raise RuntimeError("rollback after idempotency winner insert")

    with engine.begin() as transaction:
        rows = transaction.execute(select(db.action_runs)).mappings().all()
    assert rows == []


def test_action_repository_contract_updates_terminal_state_and_writebacks(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_FAILED,
            error={"type": "ExternalSystemError"},
            completed_at="2026-06-10T00:00:05Z",
        )
        stale = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_SUCCEEDED,
            error=None,
            completed_at="2026-06-10T00:00:06Z",
        )
        harness.repository.insert_action_writeback(transaction=transaction, record=_writeback_record(status="failed"))

    action_runs = harness.action_run_rows()
    writebacks = harness.writeback_rows()
    assert updated is True
    assert stale is False
    assert action_runs[0]["status"] == "failed"
    assert action_runs[0]["error"] == {"type": "ExternalSystemError"}
    assert writebacks[0]["status"] == "failed"
    assert writebacks[0]["connector_id"] == "mock_erp"


def test_action_repository_contract_persists_terminal_result_snapshot(harness: ActionHarness) -> None:
    result = {
        "actionRunId": "arun_1",
        "status": "succeeded",
        "objectEditId": "edit_1",
        "newObjectVersion": 2,
    }

    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_SUCCEEDED,
            error=None,
            completed_at="2026-06-10T00:00:05Z",
            result=result,
        )

    action_runs = harness.action_run_rows()
    assert updated is True
    assert action_runs[0]["status"] == "succeeded"
    assert action_runs[0]["result"] == result


def test_action_repository_contract_persists_outcome_unknown_writeback_fields(harness: ActionHarness) -> None:
    request = {
        "connector": "mock_erp",
        "simulated": True,
        "networkCall": False,
        "idempotency_key": "idem-1",
        "request_hash": "request-hash-1",
    }
    response = {
        "status_code": None,
        "outcome_unknown": True,
        "external_operation_id": "mock-op-idem-1",
        "remote_resource_id": None,
        "last_observed_status": "unknown",
        "reconciliation_deadline": "2026-06-10T00:00:02Z",
    }
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_OUTCOME_UNKNOWN,
            error={"type": "EXTERNAL_OUTCOME_UNKNOWN"},
            completed_at="2026-06-10T00:00:05Z",
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(status="outcome_unknown", request=request, response=response),
        )

    action_runs = harness.action_run_rows()
    writebacks = harness.writeback_rows()
    assert updated is True
    assert action_runs[0]["status"] == "outcome_unknown"
    assert writebacks[0]["status"] == "outcome_unknown"
    assert writebacks[0]["request"] == request
    assert writebacks[0]["response"] == response


def test_action_repository_contract_persists_retryable_writeback_fields(harness: ActionHarness) -> None:
    request = {
        "connector": "mock_erp",
        "simulated": True,
        "networkCall": False,
        "idempotency_key": "idem-1",
        "request_hash": "request-hash-1",
    }
    response = {
        "status_code": 503,
        "retryable": True,
        "external_system_changed": False,
        "remote_resource_id": None,
        "last_observed_status": "not_changed",
        "retry_after_seconds": 60,
        "reconciliation_deadline": None,
    }
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_RETRYABLE,
            error={"type": "EXTERNAL_RETRYABLE_WRITEBACK"},
            completed_at="2026-06-10T00:00:05Z",
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(status="retryable", request=request, response=response),
        )

    action_runs = harness.action_run_rows()
    writebacks = harness.writeback_rows()
    assert updated is True
    assert action_runs[0]["status"] == "retryable"
    assert writebacks[0]["status"] == "retryable"
    assert writebacks[0]["request"] == request
    assert writebacks[0]["response"] == response


def test_action_repository_contract_reconciles_outcome_unknown_writeback_once(harness: ActionHarness) -> None:
    original_response = {
        "status_code": None,
        "outcome_unknown": True,
        "external_operation_id": "mock-op-idem-1",
        "remote_resource_id": None,
        "last_observed_status": "unknown",
        "reconciliation_deadline": "2026-06-10T00:00:02Z",
    }
    reconciled_response = {
        **original_response,
        "status_code": 200,
        "outcome_unknown": False,
        "reconciled": True,
        "remote_resource_id": "mock-resource-idem-1",
        "last_observed_status": "succeeded",
        "reconciled_at": "2026-06-10T00:00:10Z",
    }
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(status="outcome_unknown", response=original_response),
        )
        found = harness.repository.action_writeback_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            writeback_id="wb_1",
        )
        first = harness.repository.reconcile_action_writeback(
            transaction=transaction,
            record=ActionWritebackReconciliation(
                writeback_id="wb_1",
                tenant_id="tenant-demo",
                action_run_id="arun_1",
                response=reconciled_response,
                completed_at="2026-06-10T00:00:10Z",
            ),
        )
        second = harness.repository.reconcile_action_writeback(
            transaction=transaction,
            record=ActionWritebackReconciliation(
                writeback_id="wb_1",
                tenant_id="tenant-demo",
                action_run_id="arun_1",
                response=reconciled_response,
                completed_at="2026-06-10T00:00:11Z",
            ),
        )

    assert found is not None
    assert found.status == "outcome_unknown"
    writebacks = harness.writeback_rows()
    assert first is True
    assert second is False
    assert writebacks[0]["status"] == "reconciled"
    assert writebacks[0]["response"] == reconciled_response


def test_action_repository_contract_reconcile_requires_matching_action_run(
    harness: ActionHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(status="outcome_unknown"),
        )
        updated = harness.repository.reconcile_action_writeback(
            transaction=transaction,
            record=ActionWritebackReconciliation(
                writeback_id="wb_1",
                tenant_id="tenant-demo",
                action_run_id="arun_other",
                response={"reconciled": True},
                completed_at="2026-06-10T00:00:10Z",
            ),
        )

    writebacks = harness.writeback_rows()
    assert updated is False
    assert writebacks[0]["status"] == "outcome_unknown"


def test_action_repository_contract_action_run_reconciles_once(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_OUTCOME_UNKNOWN,
            error={"type": "EXTERNAL_OUTCOME_UNKNOWN"},
            completed_at="2026-06-10T00:00:05Z",
        )
        reconciled_action = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_RECONCILED,
            error=None,
            completed_at="2026-06-10T00:00:10Z",
        )
        stale_action = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_FAILED,
            error={"type": "ExternalSystemError"},
            completed_at="2026-06-10T00:00:11Z",
        )

    action_runs = harness.action_run_rows()
    assert updated is True
    assert reconciled_action is True
    assert stale_action is False
    assert action_runs[0]["status"] == "reconciled"


def test_action_repository_contract_persists_compensation_required_writeback_fields(harness: ActionHarness) -> None:
    request = {
        "connector": "mock_erp",
        "simulated": True,
        "networkCall": False,
        "idempotency_key": "idem-1",
        "request_hash": "request-hash-1",
    }
    response = {
        "status_code": 200,
        "compensation_required": True,
        "external_operation_id": "mock-op-idem-1",
        "remote_resource_id": "mock-resource-idem-1",
        "last_observed_status": "succeeded",
        "compensation_action_type": "mock_reverse_writeback",
        "reconciliation_deadline": "2026-06-10T00:00:02Z",
    }
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        updated = harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            transition=ACTION_RUN_COMPENSATION_REQUIRED,
            error={"type": "EXTERNAL_COMPENSATION_REQUIRED"},
            completed_at="2026-06-10T00:00:05Z",
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(status="compensation_required", request=request, response=response),
        )

    action_runs = harness.action_run_rows()
    writebacks = harness.writeback_rows()
    assert updated is True
    assert action_runs[0]["status"] == "compensation_required"
    assert writebacks[0]["status"] == "compensation_required"
    assert writebacks[0]["request"] == request
    assert writebacks[0]["response"] == response


def test_action_repository_contract_lists_unresolved_writebacks(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(
                writeback_id="wb_old",
                status="outcome_unknown",
                created_at="2026-06-10T00:00:01Z",
            ),
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(
                writeback_id="wb_new",
                status="compensation_required",
                created_at="2026-06-10T00:00:03Z",
            ),
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(
                writeback_id="wb_retryable",
                status="retryable",
                created_at="2026-06-10T00:00:04Z",
            ),
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(writeback_id="wb_failed", status="failed"),
        )
        harness.repository.insert_action_writeback(
            transaction=transaction,
            record=_writeback_record(writeback_id="wb_other", tenant_id="tenant-other", status="outcome_unknown"),
        )
        rows = harness.repository.list_action_writebacks(
            transaction=transaction,
            tenant_id="tenant-demo",
            statuses=("outcome_unknown", "compensation_required", "retryable"),
            limit=3,
        )

    assert [row.writeback_id for row in rows] == ["wb_retryable", "wb_new", "wb_old"]
    assert {row.status for row in rows} == {"outcome_unknown", "compensation_required", "retryable"}


def test_action_repository_contract_aggregates_action_run_usage(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record(
                "arun_recent_a",
                actor_user_id="actor-a",
                status="succeeded",
                idempotency_key="usage-1",
                created_at="2026-06-10T00:00:05Z",
            ),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record(
                "arun_recent_b",
                actor_user_id="actor-b",
                status="failed",
                idempotency_key="usage-2",
                created_at="2026-06-11T00:00:00Z",
            ),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record(
                "arun_before_window",
                actor_user_id="actor-a",
                status="succeeded",
                idempotency_key="usage-3",
                created_at="2026-05-01T00:00:00Z",
            ),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record(
                "arun_ship",
                action_type_id="atype_ship",
                action_type_api_name="shipOrder",
                target_object_type_api_name="Shipment",
                actor_user_id="actor-a",
                status="succeeded",
                idempotency_key="usage-4",
                created_at="2026-06-11T00:00:01Z",
            ),
        )
        harness.repository.insert_action_run(
            transaction=transaction,
            record=_action_run_record(
                "arun_other_tenant",
                tenant_id="tenant-other",
                actor_user_id="actor-c",
                status="succeeded",
                idempotency_key="usage-5",
                created_at="2026-06-11T00:00:02Z",
            ),
        )
        by_action_type = harness.repository.action_run_usage(
            transaction=transaction,
            tenant_id="tenant-demo",
            since="2026-06-01T00:00:00Z",
            action_type_api_name="approveOrder",
        )
        by_target = harness.repository.action_run_usage(
            transaction=transaction,
            tenant_id="tenant-demo",
            since="2026-06-01T00:00:00Z",
            target_object_type_api_name="Order",
        )
        empty = harness.repository.action_run_usage(
            transaction=transaction,
            tenant_id="tenant-demo",
            since="2026-06-01T00:00:00Z",
            action_type_api_name="unknownAction",
        )

    assert by_action_type == {
        "status_counts": {"succeeded": 1, "failed": 1},
        "total_runs": 2,
        "distinct_actor_count": 2,
        "last_run_at": "2026-06-11T00:00:00Z",
    }
    assert by_target == by_action_type
    assert empty == {
        "status_counts": {},
        "total_runs": 0,
        "distinct_actor_count": 0,
        "last_run_at": None,
    }


def test_action_repository_contract_updates_object_target_and_records_edit(harness: ActionHarness) -> None:
    harness.add_object_record()

    with harness.transaction() as transaction:
        updated = harness.repository.update_object_target(transaction=transaction, record=_object_target_update())
        stale_update = harness.repository.update_object_target(
            transaction=transaction,
            record=_object_target_update(expected_object_version=3),
        )
        harness.repository.insert_object_edit(transaction=transaction, record=_object_edit_record())

    object_rows = harness.object_rows()
    edit_rows = harness.object_edit_rows()
    assert updated is True
    assert stale_update is False
    assert object_rows[0]["object_version"] == 4
    assert object_rows[0]["properties"] == {"status": "APPROVED"}
    assert edit_rows[0]["patch"] == {"status": "APPROVED"}


def test_action_repository_contract_queries_run_edits_and_latest_object_edit(harness: ActionHarness) -> None:
    first = _object_edit_record("edit_1")
    second = replace(
        first,
        edit_id="edit_2",
        action_run_id="arun_2",
        edit_type="create_link",
        patch={"linkType": "OrderCustomer", "toObjectId": "C-1"},
        idempotency_key="idem-2",
        created_at="2026-06-10T00:00:05Z",
    )
    other_tenant = replace(first, edit_id="edit_other", tenant_id="tenant-other")

    with harness.transaction() as transaction:
        harness.repository.insert_object_edit(transaction=transaction, record=second)
        harness.repository.insert_object_edit(transaction=transaction, record=first)
        harness.repository.insert_object_edit(transaction=transaction, record=other_tenant)
        first_run = harness.repository.object_edits_for_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
        )
        second_run = harness.repository.object_edits_for_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_2",
        )
        latest = harness.repository.latest_object_edit(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            object_id="O-1",
        )

    assert [row["id"] for row in first_run] == ["edit_1"]
    assert [row["id"] for row in second_run] == ["edit_2"]
    assert latest is not None
    assert latest["id"] == "edit_2"


def test_action_repository_contract_persists_one_log_and_revert_cas(harness: ActionHarness) -> None:
    entry = _action_log_entry_record()
    object_link = _action_log_object_record()
    other_tenant = replace(
        entry,
        log_entry_id="alog_other",
        tenant_id="tenant-other",
        action_run_id="arun_other",
        log_object_id="arun_other",
    )
    other_action = replace(
        entry,
        log_entry_id="alog_second",
        action_run_id="arun_second",
        log_object_type_api_name="[LOG] shipOrder",
        log_object_id="arun_second",
        action_type_id="atype_ship",
        action_type_api_name="shipOrder",
    )
    with harness.transaction() as transaction:
        inserted = harness.repository.insert_action_log(
            transaction=transaction,
            entry=entry,
            objects=(object_link,),
        )
        duplicate = harness.repository.insert_action_log(
            transaction=transaction,
            entry=entry,
            objects=(object_link,),
        )
        harness.repository.insert_action_log(transaction=transaction, entry=other_tenant, objects=())
        harness.repository.insert_action_log(transaction=transaction, entry=other_action, objects=())
        found = harness.repository.action_log_by_run_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
        )
        objects = harness.repository.action_log_objects(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_log_entry_id="alog_1",
        )
        page = harness.repository.list_action_logs(
            transaction=transaction,
            tenant_id="tenant-demo",
            before_created_at=None,
            before_log_id=None,
            limit=10,
        )
        filtered = harness.repository.list_action_logs(
            transaction=transaction,
            tenant_id="tenant-demo",
            before_created_at=None,
            before_log_id=None,
            limit=10,
            action_type_api_name="approveOrder",
        )
        queried = harness.repository.query_action_logs(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_api_name="approveOrder",
            filter_ast={"op": "contains", "property": "parameters", "value": "approved"},
            order_by=(
                {"property": "createdAt", "direction": "desc"},
                {"property": "actionRunId", "direction": "desc"},
            ),
            cursor=None,
            search_text="approved",
            limit=2,
        )
        aggregated = harness.repository.aggregate_action_logs(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_api_name="approveOrder",
            filter_ast={"op": "eq", "property": "status", "value": "succeeded"},
            group_by=("status",),
            metrics=({"name": "runCount", "function": "count", "property": None},),
            group_limit=10,
        )
        won = harness.repository.mark_action_log_reverted(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            reverted_by_run_id="arun_revert",
        )
        lost = harness.repository.mark_action_log_reverted(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            reverted_by_run_id="arun_revert_2",
        )

    assert inserted is not None and inserted["id"] == "alog_1"
    assert duplicate is None
    assert found is not None and found["tenant_id"] == "tenant-demo"
    assert [item["object_edit_id"] for item in objects] == ["edit_1"]
    assert {item["id"] for item in page} == {"alog_1", "alog_second"}
    assert [item["id"] for item in filtered] == ["alog_1"]
    assert [item["id"] for item in queried] == ["alog_1"]
    assert aggregated == [{"key": {"status": "succeeded"}, "metrics": {"runCount": 1}}]
    assert won is True
    assert lost is False


def test_action_repository_contract_creates_object_and_rejects_duplicate_identity(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        created = harness.repository.create_object_record(transaction=transaction, record=_object_create_write())
        duplicate = harness.repository.create_object_record(
            transaction=transaction,
            record=_object_create_write(object_record_id="obj_ship_2"),
        )

    rows = [row for row in harness.object_rows() if row["object_id"] == "S-1"]
    assert created is True
    assert duplicate is False
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "obj_ship_1"
    assert row["is_active"] and not row["deleted"]
    assert row["object_version"] == 1
    assert row["properties"] == {"carrier": "UPS"}
    assert row["base_properties"] == {}
    assert row["edit_properties"] == {"carrier": "UPS"}
    assert row["property_versions"] == {"carrier": 1}


def test_action_repository_contract_soft_deletes_object_under_cas(harness: ActionHarness) -> None:
    harness.add_object_record()

    with harness.transaction() as transaction:
        deleted = harness.repository.soft_delete_object_target(transaction=transaction, record=_object_delete_write())
        stale = harness.repository.soft_delete_object_target(
            transaction=transaction,
            record=_object_delete_write(expected_object_version=3),
        )
        restored = harness.repository.restore_object_target(
            transaction=transaction,
            record=ObjectRestoreWrite(
                object_record_id="obj_order_1",
                tenant_id="tenant-demo",
                expected_object_version=4,
                updated_at="2026-06-10T00:00:07Z",
            ),
        )
        stale_restore = harness.repository.restore_object_target(
            transaction=transaction,
            record=ObjectRestoreWrite(
                object_record_id="obj_order_1",
                tenant_id="tenant-demo",
                expected_object_version=4,
                updated_at="2026-06-10T00:00:08Z",
            ),
        )

    row = next(row for row in harness.object_rows() if row["id"] == "obj_order_1")
    assert deleted is True
    assert stale is False
    assert restored is True
    assert stale_restore is False
    assert not row["deleted"] and row["is_active"]
    assert row["object_version"] == 5
    assert row["deletion_reason"] is None


def test_action_repository_contract_creates_and_reactivates_link(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.create_object_link(transaction=transaction, record=_object_link_write())
        harness.repository.soft_delete_object_link(transaction=transaction, record=_object_link_delete_write())
        harness.repository.create_object_link(
            transaction=transaction,
            record=_object_link_write(link_record_id="lnk_2"),
        )

    rows = [
        row for row in harness.object_link_rows() if row["from_object_id"] == "O-1" and row["to_object_id"] == "S-1"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "lnk_1"
    assert row["is_active"] and not row["deleted"]
    assert row["link_version"] == 3


def test_action_repository_contract_soft_deletes_link_once(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.create_object_link(transaction=transaction, record=_object_link_write())
        deleted = harness.repository.soft_delete_object_link(
            transaction=transaction,
            record=_object_link_delete_write(),
        )
        again = harness.repository.soft_delete_object_link(
            transaction=transaction,
            record=_object_link_delete_write(),
        )

    row = next(row for row in harness.object_link_rows() if row["id"] == "lnk_1")
    assert deleted is True
    assert again is False
    assert row["deleted"] and not row["is_active"]
    assert row["link_version"] == 2


def test_object_create_and_delete_append_version_snapshots(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyActionRepository(engine)

    with engine.begin() as transaction:
        repository.create_object_record(transaction=transaction, record=_object_create_write())
    with engine.begin() as transaction:
        current = (
            transaction.execute(select(db.object_records).where(db.object_records.c.id == "obj_ship_1"))
            .mappings()
            .one()
        )
        repository.soft_delete_object_target(
            transaction=transaction,
            record=ObjectDeleteWrite(
                object_record_id="obj_ship_1",
                tenant_id="tenant-demo",
                expected_object_version=current["object_version"],
                deletion_reason="action:cleanup",
                updated_at="2026-06-10T00:00:09Z",
            ),
        )
    with engine.begin() as transaction:
        versions = (
            transaction.execute(
                select(db.object_record_versions)
                .where(db.object_record_versions.c.object_record_id == "obj_ship_1")
                .order_by(db.object_record_versions.c.object_change_sequence)
            )
            .mappings()
            .all()
        )

    assert len(versions) == 2
    assert not versions[0]["deleted"]
    assert versions[0]["object_version"] == 1
    assert versions[-1]["deleted"]
    assert versions[-1]["object_version"] == 2
