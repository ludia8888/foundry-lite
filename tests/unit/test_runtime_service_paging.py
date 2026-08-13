from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from typing import cast

import pytest
from foundry_lite.application.ports import (
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunPageCursor,
    RuntimeRunRelationRecord,
    RuntimeRunRelationRow,
    RuntimeRunSnapshot,
    RuntimeRunType,
)
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.application.services.runtime_evidence_service import RuntimeEvidenceService
from foundry_lite.application.services.runtime_run_cursors import (
    OPERATIONS_CURSOR_SIGNING_KEY_ENV,
    OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV,
    OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV,
    decode_runtime_run_cursor,
    encode_runtime_run_cursor,
)
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.security.policy import PolicyService

_OPERATOR = RequestContext(roles=("ops_manager",))


class _PagedRuntimeRepository:
    def __init__(self) -> None:
        self.requested_limits: list[int] = []
        self.rows = [
            _runtime_row("action_c", "2026-06-10T00:00:02Z"),
            _runtime_row("action_b", "2026-06-10T00:00:02Z"),
            _runtime_row("action_a", "2026-06-10T00:00:01Z"),
        ]

    def list_runs(self, *, tenant_id: str) -> object:
        del tenant_id
        raise AssertionError("operations query must not read the full run snapshot")

    def query_run_rows(
        self,
        *,
        tenant_id: str,
        run_type: RuntimeRunType,
        status: str | None,
        since: str | None,
        until: str | None,
        cursor: RuntimeRunPageCursor | None,
        limit: int,
    ) -> list[RuntimeRow]:
        del tenant_id, status, since, until
        assert run_type == "action"
        self.requested_limits.append(limit)
        rows = self.rows
        if cursor is not None:
            rows = [
                row
                for row in rows
                if (str(row["created_at"]), str(row["id"])) < (cursor["timestamp"], cursor["run_id"])
            ]
        return rows[:limit]


class _AllRunTypesRepository:
    def __init__(self) -> None:
        self.requested_types: list[RuntimeRunType] = []

    def query_run_rows(
        self,
        *,
        tenant_id: str,
        run_type: RuntimeRunType,
        status: str | None,
        since: str | None,
        until: str | None,
        cursor: RuntimeRunPageCursor | None,
        limit: int,
    ) -> list[RuntimeRow]:
        del tenant_id, status, since, until, cursor
        self.requested_types.append(run_type)
        return [
            _runtime_row(f"{run_type}_b", "2026-06-10T00:00:02Z"),
            _runtime_row(f"{run_type}_a", "2026-06-10T00:00:01Z"),
        ][:limit]


class _UnusedEngine:
    pass


class _BeginEngine:
    @contextmanager
    def begin(self):
        yield object()


class _UnusedDatasetTransactionRepository:
    def transaction_by_id(self, **_kwargs: object) -> object:
        raise AssertionError("query_runs must not read dataset transactions")

    def committed_transaction_by_version(self, **_kwargs: object) -> object:
        raise AssertionError("query_runs must not read dataset transactions")


class _UnusedDatasetQualityRepository:
    def check_results_for_transaction(self, **_kwargs: object) -> object:
        raise AssertionError("query_runs must not read dataset quality results")


class _UnusedAiRunRepository:
    def ledger_for_run(self, **_kwargs: object) -> object:
        raise AssertionError("non-AI operations should not read the AI ledger")


class _SnapshotRuntimeRepository:
    def __init__(self, snapshot: RuntimeRunSnapshot) -> None:
        self.snapshot = snapshot

    def list_runs(self, *, tenant_id: str, limit: int | None = None) -> RuntimeRunSnapshot:
        assert tenant_id == "tenant-demo"
        del limit
        return self.snapshot


class _DetailRuntimeRepository:
    def __init__(self) -> None:
        self.relation_reads: list[tuple[str, RuntimeRunType, str]] = []

    def run_row(self, *, tenant_id: str, run_type: RuntimeRunType, run_id: str) -> RuntimeRow | None:
        assert tenant_id == "tenant-demo"
        if run_type == "action" and run_id == "action_run_1":
            return _runtime_row("action_run_1", "2026-06-10T00:00:01Z")
        return None

    def run_row_any_type(self, *, tenant_id: str, run_id: str) -> tuple[RuntimeRunType, RuntimeRow] | None:
        del tenant_id, run_id
        return None

    def related_evidence_rows(
        self, *, tenant_id: str, table: RuntimeRowsTable, relation_ids: Sequence[str], limit: int
    ) -> list[RuntimeRow]:
        del tenant_id, table, relation_ids, limit
        return []

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[dict[str, object]]:
        del tenant_id, resource_id
        return []

    def run_relations_for_run(
        self,
        *,
        transaction: object,
        tenant_id: str,
        run_type: RuntimeRunType,
        run_id: str,
    ) -> list[RuntimeRunRelationRow]:
        del transaction
        self.relation_reads.append((tenant_id, run_type, run_id))
        return [
            {
                "id": "run_relation_1",
                "tenant_id": tenant_id,
                "source_run_type": "action",
                "source_run_id": run_id,
                "target_run_type": "outbox",
                "target_run_id": "outbox_1",
                "relation": "emitted",
                "resource_type": "object",
                "resource_id": "Order:O-1",
                "metadata": {"reason": "action commit outbox"},
                "created_at": "2026-06-10T00:00:02Z",
            }
        ]


class _RetryDeadLetterRepository:
    def __init__(self, update_result: dict[str, object] | None, *, deleted: bool = True) -> None:
        self.update_result = update_result
        self.deleted = deleted
        self.relations: list[dict[str, object]] = []

    def rows_for_tenant(self, **kwargs: object) -> list[dict[str, object]]:
        table = kwargs["table"]
        if table == "audit_events":
            return []
        if table == "outbox_events":
            return [{"id": "outbox-1"}]
        raise AssertionError(f"unexpected table {table}")

    def dead_letter_event_by_id(self, **_kwargs: object) -> dict[str, object]:
        return {"source_event_id": "outbox-1", "event_type": "materialization.requested", "payload": {}}

    def update_outbox_event_for_retry(self, **_kwargs: object) -> dict[str, object] | None:
        return self.update_result

    def delete_dead_letter_event(self, **_kwargs: object) -> bool:
        return self.deleted

    def insert_audit_event(self, **_kwargs: object) -> None:
        return None

    def insert_run_relation(self, **kwargs: object) -> bool:
        record = cast(RuntimeRunRelationRecord, kwargs["record"])
        self.relations.append(
            {
                "source_run_type": record.source_run_type,
                "source_run_id": record.source_run_id,
                "target_run_type": record.target_run_type,
                "target_run_id": record.target_run_id,
                "relation": record.relation,
            }
        )
        return True


def _runtime_service(repository: object) -> RuntimeService:
    engine = _UnusedEngine()
    policy = PolicyService(allow_unwired_classification_provider=True)
    ai_repository = _UnusedAiRunRepository()
    service = RuntimeService(
        engine=engine,
        policy=policy,
        runtime_repository=repository,
        ai_run_repository=ai_repository,
        dataset_transaction_repository=_UnusedDatasetTransactionRepository(),
        dataset_quality_repository=_UnusedDatasetQualityRepository(),
    )
    service.evidence_service = RuntimeEvidenceService(
        ai_run_repository=ai_repository,
        engine=engine,
        policy=policy,
        profile=RuntimeProfile(),
        runtime_repository=repository,
    )
    return service


def _runtime_service_with_engine(repository: object) -> RuntimeService:
    engine = _BeginEngine()
    policy = PolicyService(allow_unwired_classification_provider=True)
    ai_repository = _UnusedAiRunRepository()
    service = RuntimeService(
        engine=engine,
        policy=policy,
        runtime_repository=repository,
        ai_run_repository=ai_repository,
        dataset_transaction_repository=_UnusedDatasetTransactionRepository(),
        dataset_quality_repository=_UnusedDatasetQualityRepository(),
    )
    service.evidence_service = RuntimeEvidenceService(
        ai_run_repository=ai_repository,
        engine=engine,
        policy=policy,
        profile=RuntimeProfile(),
        runtime_repository=repository,
    )
    return service


def test_runtime_service_query_runs_uses_db_keyset_page_and_opaque_cursor() -> None:
    repository = _PagedRuntimeRepository()
    service = _runtime_service(repository)

    first = service.query_runs(
        ctx=RequestContext(roles=("ops_manager",)),
        run_type="action",
        status="succeeded",
        limit=1,
    )
    next_cursor = first.get("nextCursor")
    assert isinstance(next_cursor, str)
    second = service.query_runs(
        ctx=RequestContext(roles=("ops_manager",)),
        run_type="action",
        status="succeeded",
        limit=1,
        cursor=next_cursor,
    )

    assert repository.requested_limits == [2, 2]
    assert [row["id"] for row in first["actionRuns"]] == ["action_c"]
    assert [row["id"] for row in second["actionRuns"]] == ["action_b"]


def test_runtime_service_query_runs_builds_group_next_cursors() -> None:
    repository = _AllRunTypesRepository()
    service = _runtime_service(repository)

    result = service.query_runs(ctx=RequestContext(roles=("ops_manager",)), limit=1)

    assert [row["id"] for row in result["syncRuns"]] == ["sync_b"]
    assert [row["id"] for row in result["transformRuns"]] == ["transform_b"]
    assert [row["id"] for row in result["indexRuns"]] == ["index_b"]
    assert [row["id"] for row in result["actionRuns"]] == ["action_b"]
    assert [row["id"] for row in result["actionWritebacks"]] == ["action_writeback_b"]
    assert [row["id"] for row in result["materializationRuns"]] == ["materialization_b"]
    assert [row["id"] for row in result["outboxEvents"]] == ["outbox_b"]
    assert [row["id"] for row in result["deadLetterEvents"]] == ["dead_letter_b"]
    assert [row["id"] for row in result["workflowRuns"]] == ["workflow_b"]
    assert [row["id"] for row in result["aiRuns"]] == ["ai_b"]
    assert [row["id"] for row in result["auditEvents"]] == ["audit_b"]
    next_cursors = result.get("nextCursors")
    assert isinstance(next_cursors, dict)
    assert set(next_cursors) == set(repository.requested_types)


def test_runtime_service_query_runs_rejects_bad_cursor_and_large_limit() -> None:
    service = _runtime_service(_PagedRuntimeRepository())

    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, run_type="action", cursor="orc1.not-valid-base64")
    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, run_type="action", cursor="not-prefixed")
    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, limit=501)
    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, limit=0)
    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, cursor="orc1.not-valid-base64")


def test_runtime_service_query_runs_rejects_cursor_shape_mismatch() -> None:
    cursor = encode_runtime_run_cursor(
        _runtime_row("action_c", "2026-06-10T00:00:02Z"),
        actor_user_id=_OPERATOR.actor_user_id,
        run_type="action",
        status="succeeded",
        since=None,
        tenant_id=_OPERATOR.tenant_id,
        until=None,
    )
    service = _runtime_service(_PagedRuntimeRepository())

    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, run_type="transform", status="succeeded", cursor=cursor)
    with pytest.raises(ValidationFailed):
        service.query_runs(ctx=_OPERATOR, run_type="action", status="failed", cursor=cursor)
    with pytest.raises(ValidationFailed):
        encode_runtime_run_cursor(
            {"id": "run_without_timestamp"},
            actor_user_id=_OPERATOR.actor_user_id,
            run_type="action",
            status=None,
            since=None,
            tenant_id=_OPERATOR.tenant_id,
            until=None,
        )


def test_runtime_run_cursor_is_signed_scoped_expiring_and_key_rotatable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV, "ops-key-1")
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ENV, "ops-secret-1")
    cursor = encode_runtime_run_cursor(
        _runtime_row("action_c", "2026-06-10T00:00:02Z"),
        actor_user_id="operator-a",
        run_type="action",
        status="succeeded",
        since=None,
        tenant_id="tenant-a",
        until=None,
        now_epoch=100,
    )

    assert cursor.startswith("orc2.")
    assert decode_runtime_run_cursor(
        cursor,
        actor_user_id="operator-a",
        run_type="action",
        status="succeeded",
        since=None,
        tenant_id="tenant-a",
        until=None,
        now_epoch=100,
    ) == {"timestamp": "2026-06-10T00:00:02Z", "run_id": "action_c"}

    with pytest.raises(ValidationFailed, match="tenant"):
        decode_runtime_run_cursor(
            cursor,
            actor_user_id="operator-a",
            run_type="action",
            status="succeeded",
            since=None,
            tenant_id="tenant-b",
            until=None,
            now_epoch=100,
        )
    with pytest.raises(ValidationFailed, match="user"):
        decode_runtime_run_cursor(
            cursor,
            actor_user_id="operator-b",
            run_type="action",
            status="succeeded",
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=100,
        )
    with pytest.raises(ValidationFailed, match="expired"):
        decode_runtime_run_cursor(
            cursor,
            actor_user_id="operator-a",
            run_type="action",
            status="succeeded",
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=1001,
        )
    with pytest.raises(ValidationFailed, match="invalid operations cursor"):
        decode_runtime_run_cursor(
            cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
            actor_user_id="operator-a",
            run_type="action",
            status="succeeded",
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=100,
        )

    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV, "ops-key-2")
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ENV, "ops-secret-2")
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV, '{"ops-key-1":"ops-secret-1"}')
    decoded_after_rotation = decode_runtime_run_cursor(
        cursor,
        actor_user_id="operator-a",
        run_type="action",
        status="succeeded",
        since=None,
        tenant_id="tenant-a",
        until=None,
        now_epoch=100,
    )
    assert decoded_after_rotation is not None
    assert decoded_after_rotation["run_id"] == "action_c"


def test_runtime_service_observability_report_reads_tenant_snapshot() -> None:
    service = _runtime_service(_SnapshotRuntimeRepository(_empty_snapshot()))

    report = service.observability_report(ctx=_OPERATOR, observed_at="2026-06-10T00:00:00Z")

    assert report["generatedAt"] == "2026-06-10T00:00:00Z"
    assert report["summary"] == {"active": 0, "suppressed": 0, "evaluatedDetectors": 0}


def test_runtime_service_run_detail_exposes_durable_run_relations() -> None:
    repository = _DetailRuntimeRepository()
    service = _runtime_service_with_engine(repository)

    detail = service.run_detail("action", "action_run_1", ctx=_OPERATOR)

    assert repository.relation_reads == [("tenant-demo", "action", "action_run_1")]
    assert [row["id"] for row in detail["runRelations"]] == ["run_relation_1"]
    assert detail["runRelations"][0]["target_run_id"] == "outbox_1"


def test_runtime_service_run_detail_raises_not_found_for_missing_run() -> None:
    service = _runtime_service_with_engine(_DetailRuntimeRepository())

    with pytest.raises(NotFound, match="operations run not found"):
        service.run_detail("action", "missing-run", ctx=_OPERATOR)


def test_retry_dead_letter_event_rejects_outbox_retry_cas_miss() -> None:
    service = _runtime_service_with_engine(_RetryDeadLetterRepository(None))

    with pytest.raises(NotFound, match="source outbox event not found"):
        service.retry_dead_letter_event("dlq-1", ctx=_OPERATOR)


def test_retry_dead_letter_event_rejects_dead_letter_delete_race() -> None:
    repository = _RetryDeadLetterRepository({"id": "outbox-1", "status": "pending"}, deleted=False)
    service = _runtime_service_with_engine(repository)

    with pytest.raises(NotFound, match="dead-letter event not found"):
        service.retry_dead_letter_event("dlq-1", ctx=_OPERATOR)


def test_retry_dead_letter_event_records_run_relation() -> None:
    repository = _RetryDeadLetterRepository({"id": "outbox-1", "status": "pending"})
    service = _runtime_service_with_engine(repository)

    result = service.retry_dead_letter_event("dlq-1", ctx=_OPERATOR)

    assert result["status"] == "pending"
    assert repository.relations == [
        {
            "source_run_type": "dead_letter",
            "source_run_id": "dlq-1",
            "target_run_type": "outbox",
            "target_run_id": "outbox-1",
            "relation": "requeued",
        }
    ]


def _empty_snapshot() -> RuntimeRunSnapshot:
    return {
        "syncRuns": [],
        "transformRuns": [],
        "indexRuns": [],
        "actionRuns": [],
        "actionWritebacks": [],
        "materializationRuns": [],
        "outboxEvents": [],
        "deadLetterEvents": [],
        "workflowRuns": [],
        "aiRuns": [],
        "auditEvents": [],
        "objectEdits": [],
    }


def _runtime_row(run_id: str, created_at: str) -> RuntimeRow:
    return {
        "id": run_id,
        "tenant_id": "tenant-demo",
        "status": "SUCCEEDED",
        "created_at": created_at,
    }
