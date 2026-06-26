"""AI eval and release evidence repository port (AIP-lite P0k, §8.12/§15)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

AiEvalJsonObject = Mapping[str, object]
AiEvalRow = Mapping[str, object]


class AiEvalSnapshot(TypedDict):
    """Rows attached to one tenant-scoped eval run."""

    run: AiEvalRow
    results: list[AiEvalRow]


@dataclass(frozen=True)
class AiEvalSuiteRecord:
    id: str
    tenant_id: str
    suite_api_name: str
    version: str
    description: str
    axes: tuple[str, ...]
    status: str
    created_at: str


@dataclass(frozen=True)
class AiEvalCaseRecord:
    id: str
    tenant_id: str
    suite_id: str
    case_api_name: str
    axis: str
    input_json: AiEvalJsonObject
    expected_json: AiEvalJsonObject
    rubric_json: AiEvalJsonObject
    tags: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class AiEvalRunRecord:
    id: str
    tenant_id: str
    suite_id: str
    agent_version_id: str
    candidate_release_channel: str
    status: str
    min_score: float
    is_passed: bool
    summary_json: AiEvalJsonObject
    started_at: str
    completed_at: str | None


@dataclass(frozen=True)
class AiEvalResultRecord:
    id: str
    tenant_id: str
    eval_run_id: str
    case_id: str
    sample_index: int
    axis: str
    score: float
    is_passed: bool
    evaluator: str
    input_hash: str
    expected_hash: str
    actual_hash: str
    result_json: AiEvalJsonObject
    created_at: str


@dataclass(frozen=True)
class AiAgentReleaseRecord:
    id: str
    tenant_id: str
    agent_version_id: str
    release_channel: str
    eval_run_id: str
    status: str
    policy_version: str
    promoted_by: str
    promoted_at: str
    created_at: str


class AiEvalRepository(Protocol):
    """DB boundary for eval evidence and release promotion records."""

    def create_suite(self, *, transaction: TransactionContext, record: AiEvalSuiteRecord) -> None:
        """Persist one eval suite definition snapshot."""
        ...

    def suite_by_name_version(
        self, *, transaction: TransactionContext, tenant_id: str, suite_api_name: str, version: str
    ) -> AiEvalRow | None:
        """Return one tenant-scoped suite by natural version key."""
        ...

    def create_case(self, *, transaction: TransactionContext, record: AiEvalCaseRecord) -> None:
        """Persist one eval case definition snapshot."""
        ...

    def case_by_name(
        self, *, transaction: TransactionContext, tenant_id: str, suite_id: str, case_api_name: str
    ) -> AiEvalRow | None:
        """Return one tenant-scoped case by suite and case name."""
        ...

    def create_run(self, *, transaction: TransactionContext, record: AiEvalRunRecord) -> None:
        """Persist eval run start before results are written."""
        ...

    def record_result(self, *, transaction: TransactionContext, record: AiEvalResultRecord) -> None:
        """Persist one case/axis result row."""
        ...

    def complete_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        eval_run_id: str,
        transition: StatusTransition,
        is_passed: bool,
        summary_json: AiEvalJsonObject,
        completed_at: str,
    ) -> AiEvalRow | None:
        """Complete one run and return the updated row."""
        ...

    def eval_run_by_id(self, *, transaction: TransactionContext, tenant_id: str, eval_run_id: str) -> AiEvalRow | None:
        """Return one tenant-scoped eval run row."""
        ...

    def results_for_run(self, *, transaction: TransactionContext, tenant_id: str, eval_run_id: str) -> list[AiEvalRow]:
        """Return tenant-scoped results for one eval run."""
        ...

    def create_release(self, *, transaction: TransactionContext, record: AiAgentReleaseRecord) -> None:
        """Persist one eval-backed release-channel promotion."""
        ...

    def release_by_agent_channel(
        self, *, transaction: TransactionContext, tenant_id: str, agent_version_id: str, release_channel: str
    ) -> AiEvalRow | None:
        """Return the current release row for one agent/channel."""
        ...
