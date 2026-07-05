"""SQLAlchemy repository adapter for Pipeline Builder graph persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, delete, desc, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRecord,
    PipelineBranchRow,
    PipelineProposalRecord,
    PipelineProposalRow,
    PipelineRunRecord,
    PipelineRunRow,
    PipelineScheduleRecord,
    PipelineScheduleRow,
    PipelineTestResultRecord,
    PipelineTestResultRow,
    PipelineVersionRecord,
    PipelineVersionRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_guarded_update, cas_status_update


class SqlAlchemyPipelineRepository:
    """Tenant-scoped Pipeline Builder rows with CAS-guarded graph updates."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_branch_if_name_free(self, *, transaction: Any, record: PipelineBranchRecord) -> PipelineBranchRow | None:
        existing = self._open_branch_by_name(
            transaction=transaction,
            tenant_id=record.tenant_id,
            pipeline_id=record.pipeline_id,
            name=record.name,
        )
        if existing is not None:
            return None
        transaction.execute(
            db.pipeline_branches.insert().values(
                id=record.branch_id,
                tenant_id=record.tenant_id,
                pipeline_id=record.pipeline_id,
                name=record.name,
                status="open",
                base_version_id=record.base_version_id,
                base_graph=record.base_graph,
                graph=record.graph,
                graph_fingerprint=record.graph_fingerprint,
                created_by=record.created_by,
                created_at=record.created_at,
                updated_at=record.updated_at,
                rebased_at=None,
                proposal_id=None,
                merged_version_id=None,
            )
        )
        return self.branch_by_id(transaction=transaction, tenant_id=record.tenant_id, branch_id=record.branch_id)

    def branch_by_id(self, *, transaction: Any, tenant_id: str, branch_id: str) -> PipelineBranchRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_branches).where(
                    and_(db.pipeline_branches.c.tenant_id == tenant_id, db.pipeline_branches.c.id == branch_id)
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineBranchRow)

    def list_branches(
        self, *, transaction: Any, tenant_id: str, status: str | None, limit: int
    ) -> list[PipelineBranchRow]:
        conditions: list[Any] = [db.pipeline_branches.c.tenant_id == tenant_id]
        if status is not None:
            conditions.append(db.pipeline_branches.c.status == status)
        rows = (
            transaction.execute(
                select(db.pipeline_branches)
                .where(and_(*conditions))
                .order_by(desc(db.pipeline_branches.c.created_at), desc(db.pipeline_branches.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_cast_row(row, PipelineBranchRow) for row in rows]

    def latest_version(self, *, transaction: Any, tenant_id: str, pipeline_id: str) -> PipelineVersionRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_versions)
                .where(
                    and_(
                        db.pipeline_versions.c.tenant_id == tenant_id,
                        db.pipeline_versions.c.pipeline_id == pipeline_id,
                    )
                )
                .order_by(desc(db.pipeline_versions.c.version_number), desc(db.pipeline_versions.c.id))
                .limit(1)
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineVersionRow)

    def update_branch_graph(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        graph: dict[str, object],
        graph_fingerprint: str,
        updated_at: str,
    ) -> PipelineBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.pipeline_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={"graph": graph, "graph_fingerprint": graph_fingerprint, "updated_at": updated_at},
            conditions=(db.pipeline_branches.c.graph_fingerprint == expected_fingerprint,),
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def rebase_branch(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        base_version_id: str | None,
        base_graph: dict[str, object],
        graph: dict[str, object],
        graph_fingerprint: str,
        rebased_at: str,
        updated_at: str,
    ) -> PipelineBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.pipeline_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={
                "base_version_id": base_version_id,
                "base_graph": base_graph,
                "graph": graph,
                "graph_fingerprint": graph_fingerprint,
                "rebased_at": rebased_at,
                "updated_at": updated_at,
            },
            conditions=(db.pipeline_branches.c.graph_fingerprint == expected_fingerprint,),
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def set_branch_proposal(
        self, *, transaction: Any, tenant_id: str, branch_id: str, proposal_id: str, updated_at: str
    ) -> PipelineBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.pipeline_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={"proposal_id": proposal_id, "updated_at": updated_at},
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def close_branch(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        status: str,
        merged_version_id: str | None,
        updated_at: str,
    ) -> PipelineBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.pipeline_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={"status": status, "merged_version_id": merged_version_id, "updated_at": updated_at},
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def insert_proposal(self, *, transaction: Any, record: PipelineProposalRecord) -> PipelineProposalRow:
        transaction.execute(
            db.pipeline_proposals.insert().values(
                id=record.proposal_id,
                tenant_id=record.tenant_id,
                pipeline_id=record.pipeline_id,
                branch_id=record.branch_id,
                title=record.title,
                description=record.description,
                status="submitted",
                graph=record.graph,
                graph_fingerprint=record.graph_fingerprint,
                assigned_to=None,
                decision=None,
                decision_comment=None,
                decided_at=None,
                created_by=record.created_by,
                created_at=record.created_at,
                updated_at=record.created_at,
            )
        )
        row = self.proposal_by_id(transaction=transaction, tenant_id=record.tenant_id, proposal_id=record.proposal_id)
        if row is None:
            raise RuntimeError("pipeline proposal row missing after insert")
        return row

    def proposal_by_id(self, *, transaction: Any, tenant_id: str, proposal_id: str) -> PipelineProposalRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_proposals).where(
                    and_(db.pipeline_proposals.c.tenant_id == tenant_id, db.pipeline_proposals.c.id == proposal_id)
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineProposalRow)

    def list_proposals(
        self, *, transaction: Any, tenant_id: str, status: str | None, limit: int
    ) -> list[PipelineProposalRow]:
        conditions: list[Any] = [db.pipeline_proposals.c.tenant_id == tenant_id]
        if status is not None:
            conditions.append(db.pipeline_proposals.c.status == status)
        rows = (
            transaction.execute(
                select(db.pipeline_proposals)
                .where(and_(*conditions))
                .order_by(desc(db.pipeline_proposals.c.created_at), desc(db.pipeline_proposals.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_cast_row(row, PipelineProposalRow) for row in rows]

    def update_proposal_assignment(
        self, *, transaction: Any, tenant_id: str, proposal_id: str, assigned_to: str, updated_at: str
    ) -> PipelineProposalRow | None:
        return self._proposal_update(
            transaction,
            tenant_id,
            proposal_id,
            {"assigned_to": assigned_to, "status": "in_review", "updated_at": updated_at},
            allowed_statuses=("submitted", "in_review"),
        )

    def update_proposal_decision(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        proposal_id: str,
        status: str,
        decision: str,
        comment: str | None,
        decided_at: str,
        updated_at: str,
    ) -> PipelineProposalRow | None:
        values = {
            "status": status,
            "decision": decision,
            "decision_comment": comment,
            "decided_at": decided_at,
            "updated_at": updated_at,
        }
        return self._proposal_update(
            transaction,
            tenant_id,
            proposal_id,
            values,
            allowed_statuses=("submitted", "in_review"),
        )

    def withdraw_proposal(
        self, *, transaction: Any, tenant_id: str, proposal_id: str, updated_at: str
    ) -> PipelineProposalRow | None:
        return self._proposal_update(
            transaction,
            tenant_id,
            proposal_id,
            {"status": "withdrawn", "updated_at": updated_at},
            allowed_statuses=("submitted", "in_review"),
        )

    def mark_proposal_executed(
        self, *, transaction: Any, tenant_id: str, proposal_id: str, updated_at: str
    ) -> PipelineProposalRow | None:
        return self._proposal_update(
            transaction,
            tenant_id,
            proposal_id,
            {"status": "executed", "updated_at": updated_at},
            allowed_statuses=("approved",),
        )

    def insert_version(self, *, transaction: Any, record: PipelineVersionRecord) -> PipelineVersionRow:
        transaction.execute(
            db.pipeline_versions.insert().values(
                id=record.version_id,
                tenant_id=record.tenant_id,
                pipeline_id=record.pipeline_id,
                version_number=record.version_number,
                graph=record.graph,
                graph_fingerprint=record.graph_fingerprint,
                proposal_id=record.proposal_id,
                created_by=record.created_by,
                created_at=record.created_at,
                deployed_at=None,
            )
        )
        row = self.version_by_id(transaction=transaction, tenant_id=record.tenant_id, version_id=record.version_id)
        if row is None:
            raise RuntimeError("pipeline version row missing after insert")
        return row

    def version_by_id(self, *, transaction: Any, tenant_id: str, version_id: str) -> PipelineVersionRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_versions).where(
                    and_(db.pipeline_versions.c.tenant_id == tenant_id, db.pipeline_versions.c.id == version_id)
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineVersionRow)

    def list_versions(
        self, *, transaction: Any, tenant_id: str, pipeline_id: str, limit: int
    ) -> list[PipelineVersionRow]:
        rows = (
            transaction.execute(
                select(db.pipeline_versions)
                .where(
                    and_(
                        db.pipeline_versions.c.tenant_id == tenant_id,
                        db.pipeline_versions.c.pipeline_id == pipeline_id,
                    )
                )
                .order_by(desc(db.pipeline_versions.c.version_number), desc(db.pipeline_versions.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_cast_row(row, PipelineVersionRow) for row in rows]

    def mark_version_deployed(
        self, *, transaction: Any, tenant_id: str, version_id: str, deployed_at: str
    ) -> PipelineVersionRow | None:
        transaction.execute(
            update(db.pipeline_versions)
            .where(and_(db.pipeline_versions.c.tenant_id == tenant_id, db.pipeline_versions.c.id == version_id))
            .values(deployed_at=deployed_at)
        )
        return self.version_by_id(transaction=transaction, tenant_id=tenant_id, version_id=version_id)

    def insert_run(self, *, transaction: Any, record: PipelineRunRecord) -> PipelineRunRow:
        transaction.execute(
            db.pipeline_runs.insert().values(
                id=record.run_id,
                tenant_id=record.tenant_id,
                pipeline_id=record.pipeline_id,
                version_id=record.version_id,
                status=record.status,
                output_dataset_ref=record.output_dataset_ref,
                output_version_id=record.output_version_id,
                timeline=record.timeline,
                error=record.error,
                created_by=record.created_by,
                started_at=record.started_at,
                completed_at=record.completed_at,
            )
        )
        row = self.run_by_id(transaction=transaction, tenant_id=record.tenant_id, run_id=record.run_id)
        if row is None:
            raise RuntimeError("pipeline run row missing after insert")
        return row

    def run_by_id(self, *, transaction: Any, tenant_id: str, run_id: str) -> PipelineRunRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_runs).where(
                    and_(db.pipeline_runs.c.tenant_id == tenant_id, db.pipeline_runs.c.id == run_id)
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineRunRow)

    def update_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        transition: StatusTransition,
        output_dataset_ref: str | None,
        output_version_id: str | None,
        timeline: list[dict[str, object]],
        error: dict[str, object] | None,
        completed_at: str,
    ) -> PipelineRunRow | None:
        updated = cas_status_update(
            transaction,
            db.pipeline_runs,
            tenant_id=tenant_id,
            row_id=run_id,
            transition=transition,
            values={
                "output_dataset_ref": output_dataset_ref,
                "output_version_id": output_version_id,
                "timeline": timeline,
                "error": error,
                "completed_at": completed_at,
            },
        )
        if not updated:
            return None
        return self.run_by_id(transaction=transaction, tenant_id=tenant_id, run_id=run_id)

    def upsert_schedule(self, *, transaction: Any, record: PipelineScheduleRecord) -> PipelineScheduleRow:
        existing = self.schedule_by_pipeline(
            transaction=transaction,
            tenant_id=record.tenant_id,
            pipeline_id=record.pipeline_id,
        )
        if existing is None:
            transaction.execute(
                db.pipeline_schedules.insert().values(
                    id=record.schedule_id,
                    tenant_id=record.tenant_id,
                    pipeline_id=record.pipeline_id,
                    version_id=record.version_id,
                    schedule=record.schedule,
                    enabled=record.enabled,
                    updated_by=record.updated_by,
                    updated_at=record.updated_at,
                    last_tick_at=None,
                )
            )
        else:
            transaction.execute(
                update(db.pipeline_schedules)
                .where(
                    and_(
                        db.pipeline_schedules.c.tenant_id == record.tenant_id,
                        db.pipeline_schedules.c.id == existing["id"],
                    )
                )
                .values(
                    version_id=record.version_id,
                    schedule=record.schedule,
                    enabled=record.enabled,
                    updated_by=record.updated_by,
                    updated_at=record.updated_at,
                )
            )
        row = self.schedule_by_pipeline(
            transaction=transaction,
            tenant_id=record.tenant_id,
            pipeline_id=record.pipeline_id,
        )
        if row is None:
            raise RuntimeError("pipeline schedule row missing after upsert")
        return row

    def schedule_by_pipeline(self, *, transaction: Any, tenant_id: str, pipeline_id: str) -> PipelineScheduleRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_schedules).where(
                    and_(
                        db.pipeline_schedules.c.tenant_id == tenant_id,
                        db.pipeline_schedules.c.pipeline_id == pipeline_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineScheduleRow)

    def delete_schedule(self, *, transaction: Any, tenant_id: str, pipeline_id: str) -> bool:
        result = transaction.execute(
            delete(db.pipeline_schedules).where(
                and_(
                    db.pipeline_schedules.c.tenant_id == tenant_id,
                    db.pipeline_schedules.c.pipeline_id == pipeline_id,
                )
            )
        )
        return bool(result.rowcount)

    def list_due_schedules(self, *, transaction: Any, tenant_id: str, limit: int) -> list[PipelineScheduleRow]:
        rows = (
            transaction.execute(
                select(db.pipeline_schedules)
                .where(and_(db.pipeline_schedules.c.tenant_id == tenant_id, db.pipeline_schedules.c.enabled.is_(True)))
                .order_by(desc(db.pipeline_schedules.c.updated_at), desc(db.pipeline_schedules.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_cast_row(row, PipelineScheduleRow) for row in rows]

    def insert_test_result(self, *, transaction: Any, record: PipelineTestResultRecord) -> PipelineTestResultRow:
        transaction.execute(
            db.pipeline_test_results.insert().values(
                id=record.result_id,
                tenant_id=record.tenant_id,
                pipeline_id=record.pipeline_id,
                branch_id=record.branch_id,
                status=record.status,
                result=record.result,
                created_by=record.created_by,
                created_at=record.created_at,
            )
        )
        row = (
            transaction.execute(
                select(db.pipeline_test_results).where(
                    and_(
                        db.pipeline_test_results.c.tenant_id == record.tenant_id,
                        db.pipeline_test_results.c.id == record.result_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise RuntimeError("pipeline test result row missing after insert")
        return _cast_row(row, PipelineTestResultRow)

    def _open_branch_by_name(
        self, *, transaction: Any, tenant_id: str, pipeline_id: str, name: str
    ) -> PipelineBranchRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_branches).where(
                    and_(
                        db.pipeline_branches.c.tenant_id == tenant_id,
                        db.pipeline_branches.c.pipeline_id == pipeline_id,
                        db.pipeline_branches.c.name == name,
                        db.pipeline_branches.c.status == "open",
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineBranchRow)

    def _proposal_update(
        self,
        transaction: Any,
        tenant_id: str,
        proposal_id: str,
        values: Mapping[str, object],
        *,
        allowed_statuses: tuple[str, ...] | None = None,
    ) -> PipelineProposalRow | None:
        statement = update(db.pipeline_proposals).where(
            and_(db.pipeline_proposals.c.tenant_id == tenant_id, db.pipeline_proposals.c.id == proposal_id)
        )
        if allowed_statuses is not None:
            statement = statement.where(db.pipeline_proposals.c.status.in_(allowed_statuses))
        result = transaction.execute(statement.values(**values))
        if not result.rowcount:
            return None
        return self.proposal_by_id(transaction=transaction, tenant_id=tenant_id, proposal_id=proposal_id)


def _cast_row[RowT](row: Any, row_type: type[RowT]) -> RowT:
    del row_type
    return cast(RowT, dict(row))


def _row[RowT](row: Any | None, row_type: type[RowT]) -> RowT | None:
    return _cast_row(row, row_type) if row is not None else None
