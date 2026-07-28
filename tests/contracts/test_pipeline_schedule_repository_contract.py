from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports.pipeline_schedule_repository import PipelineScheduleRecord
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_repository import SqlAlchemyPipelineRepository
from sqlalchemy import create_engine, update


def test_pipeline_schedule_repository_reconciles_a_legacy_writer_update(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-schedule-contract.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineRepository(engine)
    record = _schedule_record()
    with engine.begin() as transaction:
        created = repository.upsert_schedule(transaction=transaction, record=record)
        transaction.execute(
            update(db.pipeline_schedules)
            .where(db.pipeline_schedules.c.id == created["id"])
            .values(
                schedule={"kind": "manual"},
                enabled=True,
                updated_at="2026-07-05T00:05:30Z",
            )
        )
        stale = repository.list_schedules_needing_reconciliation(
            transaction=transaction,
            tenant_id=record.tenant_id,
            observed_at="2026-07-05T00:06:00Z",
            limit=10,
        )
        reconciled = repository.reconcile_schedule_runtime(
            transaction=transaction,
            tenant_id=record.tenant_id,
            schedule_id=record.schedule_id,
            expected_updated_at="2026-07-05T00:05:30Z",
            observed_at="2026-07-05T00:06:00Z",
            schedule={"triggerType": "interval", "timezone": "UTC", "intervalSeconds": 60},
            status="active",
            trigger_type="interval",
            timezone="UTC",
            next_due_at="2026-07-05T00:06:00Z",
            paused_reason=None,
        )
        due = repository.list_due_schedules(
            transaction=transaction,
            tenant_id=record.tenant_id,
            due_at="2026-07-05T00:06:00Z",
            limit=10,
        )

    assert [row["id"] for row in stale] == [record.schedule_id]
    assert reconciled is not None
    assert reconciled["runtime_config_updated_at"] == "2026-07-05T00:05:30Z"
    assert [row["id"] for row in due] == [record.schedule_id]


def _schedule_record() -> PipelineScheduleRecord:
    return PipelineScheduleRecord(
        schedule_id="schedule-a",
        tenant_id="tenant-a",
        pipeline_id="pipeline-a",
        version_id="version-a",
        schedule={"triggerType": "interval", "timezone": "UTC", "intervalSeconds": 300},
        enabled=True,
        status="active",
        trigger_type="interval",
        timezone="UTC",
        next_due_at="2026-07-05T00:10:00Z",
        updated_by="user-a",
        updated_at="2026-07-05T00:00:00Z",
    )
