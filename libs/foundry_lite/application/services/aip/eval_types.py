"""Typed DTOs for AIP eval and release guard services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

EvalJsonObject = Mapping[str, object]


@dataclass(frozen=True)
class EvalCaseInput:
    """One deterministic eval observation for an agent candidate."""

    case_api_name: str
    axis: str
    input_json: EvalJsonObject
    expected_json: EvalJsonObject
    actual_json: EvalJsonObject
    rubric_json: EvalJsonObject = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    sample_index: int = 1
    evaluator: str = "exact_subset_v1"
    weight: float = 1.0


@dataclass(frozen=True)
class EvalRunRequest:
    eval_run_id: str
    suite_api_name: str
    suite_version: str
    suite_description: str
    agent_version_id: str
    candidate_release_channel: str
    cases: tuple[EvalCaseInput, ...]
    min_score: float = 1.0
    required_axes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalCaseResult:
    case_api_name: str
    axis: str
    sample_index: int
    score: float
    is_passed: bool
    result_hash: str
    reason: str | None


@dataclass(frozen=True)
class EvalRunResult:
    eval_run_id: str
    suite_id: str
    agent_version_id: str
    status: str
    score: float
    is_passed: bool
    summary_json: EvalJsonObject
    case_results: tuple[EvalCaseResult, ...]


@dataclass(frozen=True)
class ReleasePromotionRequest:
    agent_version_id: str
    target_release_channel: str
    eval_run_id: str
    policy_version: str = "release-policy-v1"


@dataclass(frozen=True)
class ReleasePromotionResult:
    release_id: str
    agent_version_id: str
    release_channel: str
    eval_run_id: str
    status: str


@dataclass
class AiEvalError(Exception):
    """Typed fail-closed eval/release rejection."""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)
