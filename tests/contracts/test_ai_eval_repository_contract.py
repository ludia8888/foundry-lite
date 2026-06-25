"""Contract tests for AIP eval/release repository records."""

from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports.ai_eval_repository import (
    AiAgentReleaseRecord,
    AiEvalCaseRecord,
    AiEvalResultRecord,
    AiEvalRunRecord,
    AiEvalSuiteRecord,
)
from foundry_lite.application.ports.transaction_context import AI_EVAL_RUN_PASSED
from foundry_lite.infrastructure.repositories.ai_eval_repository import SqlAlchemyAiEvalRepository
from foundry_lite.infrastructure.schema import create_database
from sqlalchemy import create_engine


def test_ai_eval_repository_round_trips_sqlite_rows() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_database(engine)
    repo = SqlAlchemyAiEvalRepository(engine)

    with engine.begin() as transaction:
        repo.create_suite(
            transaction=transaction,
            record=AiEvalSuiteRecord(
                id="suite-1",
                tenant_id="tenant-demo",
                suite_api_name="order-copilot-release",
                version="v1",
                description="release suite",
                axes=("action", "security"),
                status="active",
                created_at="2026-06-26T00:00:00Z",
            ),
        )
        repo.create_case(
            transaction=transaction,
            record=AiEvalCaseRecord(
                id="case-1",
                tenant_id="tenant-demo",
                suite_id="suite-1",
                case_api_name="security-denies-cross-tenant",
                axis="security",
                input_json={"tenant": "tenant-demo"},
                expected_json={"decision": "denied"},
                rubric_json={"metric": "exact_subset_v1"},
                tags=("security",),
                created_at="2026-06-26T00:00:00Z",
            ),
        )
        repo.create_run(
            transaction=transaction,
            record=AiEvalRunRecord(
                id="eval-run-1",
                tenant_id="tenant-demo",
                suite_id="suite-1",
                agent_version_id="agent-v1",
                candidate_release_channel="stable",
                status="running",
                min_score=1.0,
                is_passed=False,
                summary_json={},
                started_at="2026-06-26T00:00:00Z",
                completed_at=None,
            ),
        )
        repo.record_result(
            transaction=transaction,
            record=AiEvalResultRecord(
                id="result-1",
                tenant_id="tenant-demo",
                eval_run_id="eval-run-1",
                case_id="case-1",
                sample_index=1,
                axis="security",
                score=1.0,
                is_passed=True,
                evaluator="exact_subset_v1",
                input_hash="sha256:input",
                expected_hash="sha256:expected",
                actual_hash="sha256:actual",
                result_json={"resultHash": "sha256:result"},
                created_at="2026-06-26T00:00:00Z",
            ),
        )
        updated = repo.complete_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            eval_run_id="eval-run-1",
            transition=AI_EVAL_RUN_PASSED,
            is_passed=True,
            summary_json={"score": 1.0, "passed": True},
            completed_at="2026-06-26T00:01:00Z",
        )
        repo.create_release(
            transaction=transaction,
            record=AiAgentReleaseRecord(
                id="release-1",
                tenant_id="tenant-demo",
                agent_version_id="agent-v1",
                release_channel="stable",
                eval_run_id="eval-run-1",
                status="active",
                policy_version="release-policy-v1",
                promoted_by="user-1",
                promoted_at="2026-06-26T00:01:00Z",
                created_at="2026-06-26T00:01:00Z",
            ),
        )

        assert updated is not None and updated["passed"] is True
        assert (
            repo.suite_by_name_version(
                transaction=transaction, tenant_id="tenant-demo", suite_api_name="order-copilot-release", version="v1"
            )
            is not None
        )
        assert (
            len(repo.results_for_run(transaction=transaction, tenant_id="tenant-demo", eval_run_id="eval-run-1")) == 1
        )
        assert (
            repo.release_by_agent_channel(
                transaction=transaction,
                tenant_id="tenant-demo",
                agent_version_id="agent-v1",
                release_channel="stable",
            )["eval_run_id"]
            == "eval-run-1"
        )
        assert repo.eval_run_by_id(transaction=transaction, tenant_id="tenant-other", eval_run_id="eval-run-1") is None


def test_ai_eval_migration_applies_postgres_rls_to_tenant_tables() -> None:
    migration = Path("migrations/versions/b9d1f3a5c7e9_aip_ai_evals_release.py").read_text(encoding="utf-8")

    for table_name in (
        "ai_eval_suites",
        "ai_eval_cases",
        "ai_eval_runs",
        "ai_eval_results",
        "ai_agent_releases",
    ):
        assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY" in migration
        assert f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY" in migration
        assert f"CREATE POLICY {table_name}_tenant_isolation" in migration
