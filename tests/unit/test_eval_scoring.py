"""Unit tests for AIP eval deterministic scoring helpers."""

from __future__ import annotations

from foundry_lite.application.services.aip.eval_scoring import axis_passed, evaluate_cases, summarize
from foundry_lite.application.services.aip.eval_types import EvalCaseInput, EvalRunRequest


def _case(
    case_api_name: str,
    axis: str,
    actual: dict[str, object],
    *,
    expected: dict[str, object] | None = None,
    sample_index: int = 1,
    weight: float = 1.0,
) -> EvalCaseInput:
    return EvalCaseInput(
        case_api_name=case_api_name,
        axis=axis,
        input_json={"prompt": case_api_name},
        expected_json=expected if expected is not None else actual,
        actual_json=actual,
        rubric_json={"metric": "exact_subset_v1"},
        sample_index=sample_index,
        weight=weight,
    )


def _request(cases: tuple[EvalCaseInput, ...], *, required_axes: tuple[str, ...] = ()) -> EvalRunRequest:
    return EvalRunRequest(
        eval_run_id="run-1",
        suite_api_name="suite",
        suite_version="v1",
        suite_description="",
        agent_version_id="agent-v1",
        candidate_release_channel="stable",
        cases=cases,
        min_score=1.0,
        required_axes=required_axes,
    )


def test_evaluate_cases_passes_on_expected_subset_match() -> None:
    results = evaluate_cases(
        (_case("a", "security", {"decision": "denied", "extra": 1}, expected={"decision": "denied"}),)
    )

    assert results[0].is_passed is True
    assert results[0].score == 1.0


def test_evaluate_cases_flags_repeated_run_variance() -> None:
    results = evaluate_cases(
        (
            _case("same", "security", {"decision": "denied"}, sample_index=1),
            _case("same", "security", {"decision": "allowed"}, sample_index=2),
        )
    )

    assert results[1].reason == "repeated_run_variance"


def test_summarize_fails_closed_on_missing_required_axis() -> None:
    results = evaluate_cases((_case("a", "answer", {"answer": "ok"}),))
    summary = summarize(results, _request((_case("a", "answer", {"answer": "ok"}),), required_axes=("security",)))

    assert summary["passed"] is False
    assert summary["missingRequiredAxes"] == ["security"]


def test_axis_passed_requires_passed_flag() -> None:
    assert axis_passed({"passed": True}) is True
    assert axis_passed({"passed": False}) is False
    assert axis_passed(None) is False
