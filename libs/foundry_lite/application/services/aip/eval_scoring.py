"""Deterministic scoring and axis-summary helpers for the AIP eval service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.eval_identifiers import hash_json
from foundry_lite.application.services.aip.eval_types import (
    AiEvalError,
    EvalCaseInput,
    EvalCaseResult,
    EvalJsonObject,
    EvalRunRequest,
)


def evaluate_cases(cases: Sequence[EvalCaseInput]) -> list[EvalCaseResult]:
    seen_hashes: dict[str, str] = {}
    results: list[EvalCaseResult] = []
    for case in cases:
        actual_hash = hash_json(case.actual_json)
        variance_reason = _variance_reason(seen_hashes, case.case_api_name, actual_hash)
        expected_matched = _contains_subset(case.actual_json, case.expected_json)
        passed = expected_matched and variance_reason is None
        reason = None if passed else variance_reason or "expected_output_mismatch"
        score = 1.0 if passed else 0.0
        results.append(_case_result(case, score=score, is_passed=passed, reason=reason))
    return results


def summarize(results: Sequence[EvalCaseResult], request: EvalRunRequest) -> EvalJsonObject:
    weighted_score = _weighted_score(results, request.cases)
    axis_summary = _axis_summary(results)
    missing_axes = sorted(set(request.required_axes) - set(axis_summary))
    variance_failures = sum(1 for result in results if result.reason == "repeated_run_variance")
    passed = weighted_score >= request.min_score and not missing_axes and variance_failures == 0
    passed = passed and all(axis_passed(axis) for axis in axis_summary.values())
    return {
        "score": weighted_score,
        "passed": passed,
        "caseCount": len(results),
        "failedCaseCount": sum(1 for result in results if not result.is_passed),
        "varianceFailures": variance_failures,
        "missingRequiredAxes": missing_axes,
        "axes": axis_summary,
        "minScore": request.min_score,
    }


def axis_passed(axis_summary: object) -> bool:
    return isinstance(axis_summary, Mapping) and axis_summary.get("passed") is True


def _weighted_score(results: Sequence[EvalCaseResult], cases: Sequence[EvalCaseInput]) -> float:
    total_weight = sum(case.weight for case in cases)
    if total_weight == 0.0:
        raise AiEvalError("invalid_weight", "at least one eval case must have positive weight")
    score = sum(result.score * case.weight for result, case in zip(results, cases, strict=True)) / total_weight
    return round(score, 6)


def _axis_summary(results: Sequence[EvalCaseResult]) -> dict[str, object]:
    axes: dict[str, list[EvalCaseResult]] = {}
    for result in results:
        axes.setdefault(result.axis, []).append(result)
    return {
        axis: {
            "passed": all(result.is_passed for result in axis_results),
            "score": round(sum(result.score for result in axis_results) / len(axis_results), 6),
            "caseCount": len(axis_results),
        }
        for axis, axis_results in sorted(axes.items())
    }


def _case_result(case: EvalCaseInput, *, score: float, is_passed: bool, reason: str | None) -> EvalCaseResult:
    result_hash = hash_json({"case": case.case_api_name, "axis": case.axis, "actual": case.actual_json})
    return EvalCaseResult(
        case_api_name=case.case_api_name,
        axis=case.axis,
        sample_index=case.sample_index,
        score=score,
        is_passed=is_passed,
        result_hash=result_hash,
        reason=reason,
    )


def _variance_reason(seen_hashes: dict[str, str], case_api_name: str, actual_hash: str) -> str | None:
    previous = seen_hashes.setdefault(case_api_name, actual_hash)
    if previous != actual_hash:
        return "repeated_run_variance"
    return None


def _contains_subset(actual: object, expected: object) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(key in actual and _contains_subset(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return actual == expected
    return actual == expected
