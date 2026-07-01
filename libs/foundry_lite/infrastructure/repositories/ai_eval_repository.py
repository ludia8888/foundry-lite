"""SQLAlchemy repository adapter for ai eval repository persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select

from foundry_lite.application.ports.ai_eval_repository import (
    AiAgentReleaseRecord,
    AiEvalCaseRecord,
    AiEvalJsonObject,
    AiEvalResultRecord,
    AiEvalRow,
    AiEvalRunRecord,
    AiEvalSuiteRecord,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyAiEvalRepository:
    """SQLAlchemy implementation of AIP eval evidence and release promotion records."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create_suite(self, *, transaction: Any, record: AiEvalSuiteRecord) -> None:
        transaction.execute(
            insert(db.ai_eval_suites).values(
                id=record.id,
                tenant_id=record.tenant_id,
                suite_api_name=record.suite_api_name,
                version=record.version,
                description=record.description,
                axes_json=list(record.axes),
                status=record.status,
                created_at=record.created_at,
            )
        )

    def suite_by_name_version(
        self, *, transaction: Any, tenant_id: str, suite_api_name: str, version: str
    ) -> AiEvalRow | None:
        row = (
            transaction.execute(
                select(db.ai_eval_suites).where(
                    and_(
                        db.ai_eval_suites.c.tenant_id == tenant_id,
                        db.ai_eval_suites.c.suite_api_name == suite_api_name,
                        db.ai_eval_suites.c.version == version,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row_or_none(row)

    def create_case(self, *, transaction: Any, record: AiEvalCaseRecord) -> None:
        transaction.execute(
            insert(db.ai_eval_cases).values(
                id=record.id,
                tenant_id=record.tenant_id,
                suite_id=record.suite_id,
                case_api_name=record.case_api_name,
                axis=record.axis,
                input_json=dict(record.input_json),
                expected_json=dict(record.expected_json),
                rubric_json=dict(record.rubric_json),
                tags_json=list(record.tags),
                created_at=record.created_at,
            )
        )

    def case_by_name(self, *, transaction: Any, tenant_id: str, suite_id: str, case_api_name: str) -> AiEvalRow | None:
        row = (
            transaction.execute(
                select(db.ai_eval_cases).where(
                    and_(
                        db.ai_eval_cases.c.tenant_id == tenant_id,
                        db.ai_eval_cases.c.suite_id == suite_id,
                        db.ai_eval_cases.c.case_api_name == case_api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row_or_none(row)

    def create_run(self, *, transaction: Any, record: AiEvalRunRecord) -> None:
        transaction.execute(
            insert(db.ai_eval_runs).values(
                id=record.id,
                tenant_id=record.tenant_id,
                suite_id=record.suite_id,
                agent_version_id=record.agent_version_id,
                candidate_release_channel=record.candidate_release_channel,
                status=record.status,
                min_score=record.min_score,
                passed=record.is_passed,
                summary_json=dict(record.summary_json),
                started_at=record.started_at,
                completed_at=record.completed_at,
            )
        )

    def record_result(self, *, transaction: Any, record: AiEvalResultRecord) -> None:
        transaction.execute(
            insert(db.ai_eval_results).values(
                id=record.id,
                tenant_id=record.tenant_id,
                eval_run_id=record.eval_run_id,
                case_id=record.case_id,
                sample_index=record.sample_index,
                axis=record.axis,
                score=record.score,
                passed=record.is_passed,
                evaluator=record.evaluator,
                input_hash=record.input_hash,
                expected_hash=record.expected_hash,
                actual_hash=record.actual_hash,
                result_json=dict(record.result_json),
                created_at=record.created_at,
            )
        )

    def complete_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        eval_run_id: str,
        transition: StatusTransition,
        is_passed: bool,
        summary_json: AiEvalJsonObject,
        completed_at: str,
    ) -> AiEvalRow | None:
        cas_status_update(
            transaction,
            db.ai_eval_runs,
            tenant_id=tenant_id,
            row_id=eval_run_id,
            transition=transition,
            values={"passed": is_passed, "summary_json": dict(summary_json), "completed_at": completed_at},
        )
        return self.eval_run_by_id(transaction=transaction, tenant_id=tenant_id, eval_run_id=eval_run_id)

    def eval_run_by_id(self, *, transaction: Any, tenant_id: str, eval_run_id: str) -> AiEvalRow | None:
        row = (
            transaction.execute(
                select(db.ai_eval_runs).where(
                    and_(db.ai_eval_runs.c.tenant_id == tenant_id, db.ai_eval_runs.c.id == eval_run_id)
                )
            )
            .mappings()
            .first()
        )
        return _row_or_none(row)

    def results_for_run(self, *, transaction: Any, tenant_id: str, eval_run_id: str) -> list[AiEvalRow]:
        rows = (
            transaction.execute(
                select(db.ai_eval_results)
                .where(
                    and_(
                        db.ai_eval_results.c.tenant_id == tenant_id,
                        db.ai_eval_results.c.eval_run_id == eval_run_id,
                    )
                )
                .order_by(db.ai_eval_results.c.case_id, db.ai_eval_results.c.sample_index)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def create_release(self, *, transaction: Any, record: AiAgentReleaseRecord) -> None:
        transaction.execute(
            insert(db.ai_agent_releases).values(
                id=record.id,
                tenant_id=record.tenant_id,
                agent_version_id=record.agent_version_id,
                release_channel=record.release_channel,
                eval_run_id=record.eval_run_id,
                status=record.status,
                policy_version=record.policy_version,
                promoted_by=record.promoted_by,
                promoted_at=record.promoted_at,
                created_at=record.created_at,
            )
        )

    def release_by_agent_channel(
        self, *, transaction: Any, tenant_id: str, agent_version_id: str, release_channel: str
    ) -> AiEvalRow | None:
        row = (
            transaction.execute(
                select(db.ai_agent_releases).where(
                    and_(
                        db.ai_agent_releases.c.tenant_id == tenant_id,
                        db.ai_agent_releases.c.agent_version_id == agent_version_id,
                        db.ai_agent_releases.c.release_channel == release_channel,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row_or_none(row)


def _row_or_none(row: Any) -> AiEvalRow | None:
    if row is None:
        return None
    return cast(AiEvalRow, dict(row))
