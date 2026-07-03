from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.ai_eval_repository import (
    AiAgentReleaseRecord,
    AiEvalCaseRecord,
    AiEvalResultRecord,
    AiEvalRunRecord,
    AiEvalSuiteRecord,
)
from foundry_lite.application.ports.transaction_context import (
    AI_EVAL_RUN_FAILED,
    AI_EVAL_RUN_PASSED,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.eval_coercion import json_object, number_field
from foundry_lite.application.services.aip.eval_identifiers import (
    ai_agent_release_id,
    ai_eval_case_id,
    ai_eval_result_id,
    ai_eval_suite_id,
    hash_json,
)
from foundry_lite.application.services.aip.eval_runtime_events import (
    EvalRuntimeBoundary,
    eval_run_completed_payload,
    release_promoted_payload,
)
from foundry_lite.application.services.aip.eval_scoring import axis_passed, evaluate_cases, summarize
from foundry_lite.application.services.aip.eval_types import (
    AiEvalError,
    EvalCaseInput,
    EvalCaseResult,
    EvalJsonObject,
    EvalRunRequest,
    EvalRunResult,
    ReleasePromotionRequest,
    ReleasePromotionResult,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

_SUPPORTED_AXES = frozenset({"retrieval", "answer", "citation", "tool", "action", "security", "operations"})
_RELEASE_CHANNELS = frozenset({"draft", "dev", "canary", "stable", "sunset"})
_STABLE_REQUIRED_AXES = frozenset({"security", "action"})


class EvalService(CoreService):
    """Record deterministic eval evidence and gate release promotion."""

    required_dependencies = ("engine", "ai_eval_repository", "policy")
    required_collaborators = ("runtime_service",)
    runtime_service: EvalRuntimeBoundary

    def run_eval(self, ctx: RequestContext, request: EvalRunRequest) -> EvalRunResult:
        self.policy.require(ctx, "aip:evals:run")
        _validate_eval_request(request)
        suite_id = ai_eval_suite_id(ctx.tenant_id, request.suite_api_name, request.suite_version)
        started_at = _now()
        case_results = evaluate_cases(request.cases)
        summary = summarize(case_results, request)
        passed = bool(summary["passed"])
        status = "passed" if passed else "failed"
        completed_at = _now()
        with self.engine.begin() as transaction:
            self._ensure_suite(ctx, transaction, request, suite_id, started_at)
            for case in request.cases:
                self._ensure_case(ctx, transaction, suite_id, case, started_at)
            self._create_run(ctx, transaction, request, suite_id, started_at)
            self._record_results(ctx, transaction, request, suite_id, case_results, completed_at)
            self._complete_run(transaction, ctx, request, is_passed=passed, summary=summary, completed_at=completed_at)
            payload = eval_run_completed_payload(request, suite_id, status, passed, summary)
            self._emit_runtime_event(
                transaction,
                ctx,
                event_type="ai.eval_run.completed",
                aggregate_type="ai_eval_run",
                aggregate_id=request.eval_run_id,
                action="run_eval",
                payload=payload,
                idempotency_key=request.eval_run_id,
            )
        return _eval_run_result(request, suite_id, status, passed, summary, case_results)

    def _complete_run(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        request: EvalRunRequest,
        *,
        is_passed: bool,
        summary: EvalJsonObject,
        completed_at: str,
    ) -> None:
        completed = self.ai_eval_repository.complete_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            eval_run_id=request.eval_run_id,
            transition=AI_EVAL_RUN_PASSED if is_passed else AI_EVAL_RUN_FAILED,
            is_passed=is_passed,
            summary_json=summary,
            completed_at=completed_at,
        )
        if completed is None:
            raise ConflictDetected(
                "eval run is not in a completable state",
                details={"evalRunId": request.eval_run_id},
            )

    def promote_release(self, ctx: RequestContext, request: ReleasePromotionRequest) -> ReleasePromotionResult:
        self.policy.require(ctx, "aip:releases:promote")
        _validate_release_request(request)
        release_id = ai_agent_release_id(ctx.tenant_id, request.agent_version_id, request.target_release_channel)
        with self.engine.begin() as transaction:
            run = self.ai_eval_repository.eval_run_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, eval_run_id=request.eval_run_id
            )
            _require_releasable_run(run, request)
            existing = self.ai_eval_repository.release_by_agent_channel(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                agent_version_id=request.agent_version_id,
                release_channel=request.target_release_channel,
            )
            if existing is not None:
                return _existing_release(existing, request)
            winner = self._persist_release(transaction, ctx, request, release_id)
            if winner is not None:
                return _existing_release(winner, request)
        return _promotion_result(request, release_id)

    def _persist_release(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        request: ReleasePromotionRequest,
        release_id: str,
    ) -> Mapping[str, object] | None:
        # Two first-time promotions can both pass the existence check and race to
        # insert the same (tenant, agent_version, channel) release; the loser
        # resolves to the winning row instead of surfacing a 500.
        winner = self.ai_eval_repository.create_release_if_absent(
            transaction=transaction,
            record=_release_record(ctx, request, release_id, _now()),
        )
        if winner is not None:
            return winner
        self._emit_runtime_event(
            transaction,
            ctx,
            event_type="ai.agent_release.promoted",
            aggregate_type="ai_agent_release",
            aggregate_id=release_id,
            action="promote_release",
            payload=release_promoted_payload(request, release_id),
            idempotency_key=release_id,
        )
        return None

    def _emit_runtime_event(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        action: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> None:
        self.runtime_service._outbox(
            transaction,
            ctx,
            event_type,
            aggregate_type,
            aggregate_id,
            payload,
            idempotency_key=idempotency_key,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type=event_type,
            resource_type=aggregate_type,
            resource_id=aggregate_id,
            action=action,
            after_ref=payload,
            correlation_id=ctx.request_id,
        )

    def _ensure_suite(
        self,
        ctx: RequestContext,
        transaction: TransactionContext,
        request: EvalRunRequest,
        suite_id: str,
        created_at: str,
    ) -> None:
        axes = tuple(sorted({case.axis for case in request.cases}))
        existing = self.ai_eval_repository.suite_by_name_version(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            suite_api_name=request.suite_api_name,
            version=request.suite_version,
        )
        if existing is not None:
            _require_same_tuple(existing.get("axes_json"), axes, "suite_definition_conflict")
            return
        self.ai_eval_repository.create_suite(
            transaction=transaction,
            record=AiEvalSuiteRecord(
                id=suite_id,
                tenant_id=ctx.tenant_id,
                suite_api_name=request.suite_api_name,
                version=request.suite_version,
                description=request.suite_description,
                axes=axes,
                status="active",
                created_at=created_at,
            ),
        )

    def _ensure_case(
        self, ctx: RequestContext, transaction: TransactionContext, suite_id: str, case: EvalCaseInput, created_at: str
    ) -> None:
        case_id = ai_eval_case_id(suite_id, case.case_api_name)
        existing = self.ai_eval_repository.case_by_name(
            transaction=transaction, tenant_id=ctx.tenant_id, suite_id=suite_id, case_api_name=case.case_api_name
        )
        if existing is not None:
            _require_same_case(existing, case)
            return
        self.ai_eval_repository.create_case(
            transaction=transaction,
            record=AiEvalCaseRecord(
                id=case_id,
                tenant_id=ctx.tenant_id,
                suite_id=suite_id,
                case_api_name=case.case_api_name,
                axis=case.axis,
                input_json=case.input_json,
                expected_json=case.expected_json,
                rubric_json=case.rubric_json,
                tags=case.tags,
                created_at=created_at,
            ),
        )

    def _create_run(
        self,
        ctx: RequestContext,
        transaction: TransactionContext,
        request: EvalRunRequest,
        suite_id: str,
        started_at: str,
    ) -> None:
        self.ai_eval_repository.create_run(
            transaction=transaction,
            record=AiEvalRunRecord(
                id=request.eval_run_id,
                tenant_id=ctx.tenant_id,
                suite_id=suite_id,
                agent_version_id=request.agent_version_id,
                candidate_release_channel=request.candidate_release_channel,
                status="running",
                min_score=request.min_score,
                is_passed=False,
                summary_json={},
                started_at=started_at,
                completed_at=None,
            ),
        )

    def _record_results(
        self,
        ctx: RequestContext,
        transaction: TransactionContext,
        request: EvalRunRequest,
        suite_id: str,
        results: Sequence[EvalCaseResult],
        created_at: str,
    ) -> None:
        for case, result in zip(request.cases, results, strict=True):
            self.ai_eval_repository.record_result(
                transaction=transaction,
                record=AiEvalResultRecord(
                    id=ai_eval_result_id(request.eval_run_id, case.case_api_name, case.sample_index, case.axis),
                    tenant_id=ctx.tenant_id,
                    eval_run_id=request.eval_run_id,
                    case_id=ai_eval_case_id(suite_id, case.case_api_name),
                    sample_index=case.sample_index,
                    axis=case.axis,
                    score=result.score,
                    is_passed=result.is_passed,
                    evaluator=case.evaluator,
                    input_hash=hash_json(case.input_json),
                    expected_hash=hash_json(case.expected_json),
                    actual_hash=hash_json(case.actual_json),
                    result_json={"reason": result.reason, "resultHash": result.result_hash},
                    created_at=created_at,
                ),
            )


def _validate_eval_request(request: EvalRunRequest) -> None:
    _required_text(request.eval_run_id, "eval_run_id")
    _required_text(request.suite_api_name, "suite_api_name")
    _required_text(request.suite_version, "suite_version")
    _required_text(request.agent_version_id, "agent_version_id")
    if request.candidate_release_channel not in _RELEASE_CHANNELS:
        raise AiEvalError("unsupported_release_channel", "candidate release channel is not supported")
    if not 0.0 <= request.min_score <= 1.0:
        raise AiEvalError("invalid_min_score", "min_score must be between 0.0 and 1.0")
    if not request.cases:
        raise AiEvalError("empty_eval_suite", "eval suite must contain at least one case")
    for axis in request.required_axes:
        _require_supported_axis(axis)
    for case in request.cases:
        _validate_case(case)


def _eval_run_result(
    request: EvalRunRequest,
    suite_id: str,
    status: str,
    is_passed: bool,
    summary: EvalJsonObject,
    case_results: Sequence[EvalCaseResult],
) -> EvalRunResult:
    return EvalRunResult(
        eval_run_id=request.eval_run_id,
        suite_id=suite_id,
        agent_version_id=request.agent_version_id,
        status=status,
        score=number_field(summary, "score"),
        is_passed=is_passed,
        summary_json=summary,
        case_results=tuple(case_results),
    )


def _validate_case(case: EvalCaseInput) -> None:
    _required_text(case.case_api_name, "case_api_name")
    _require_supported_axis(case.axis)
    if case.sample_index <= 0:
        raise AiEvalError("invalid_sample_index", "sample_index must be positive")
    if not 0.0 <= case.weight:
        raise AiEvalError("invalid_weight", "case weight must be non-negative")


def _validate_release_request(request: ReleasePromotionRequest) -> None:
    _required_text(request.agent_version_id, "agent_version_id")
    _required_text(request.eval_run_id, "eval_run_id")
    _required_text(request.policy_version, "policy_version")
    if request.target_release_channel not in _RELEASE_CHANNELS:
        raise AiEvalError("unsupported_release_channel", "target release channel is not supported")


def _require_releasable_run(run: Mapping[str, object] | None, request: ReleasePromotionRequest) -> None:
    if run is None:
        raise AiEvalError("eval_run_not_found", "eval run was not found")
    if run.get("agent_version_id") != request.agent_version_id:
        raise AiEvalError("agent_version_mismatch", "eval run belongs to a different agent version")
    if run.get("candidate_release_channel") != request.target_release_channel:
        raise AiEvalError("release_channel_mismatch", "eval run was recorded for a different release channel")
    if run.get("status") != "passed" or run.get("passed") is not True:
        raise AiEvalError("eval_run_not_passed", "release promotion requires a passed eval run")
    _require_stable_release_axes(run, request.target_release_channel)


def _require_stable_release_axes(run: Mapping[str, object], target_release_channel: str) -> None:
    if target_release_channel != "stable":
        return
    summary = run.get("summary_json")
    if not isinstance(summary, Mapping):
        raise AiEvalError("stable_requires_eval_summary", "stable release requires eval axis summary")
    axes = summary.get("axes") if isinstance(summary, Mapping) else None
    if not isinstance(axes, Mapping):
        raise AiEvalError("stable_requires_eval_summary", "stable release requires eval axis summary")
    missing = sorted(axis for axis in _STABLE_REQUIRED_AXES if not axis_passed(axes.get(axis)))
    if missing:
        raise AiEvalError("stable_requires_security_action_eval", "stable release requires security and action axes")
    if number_field(summary, "varianceFailures") != 0:
        raise AiEvalError("stable_requires_deterministic_eval", "stable release requires zero repeated-run variance")


def _existing_release(existing: Mapping[str, object], request: ReleasePromotionRequest) -> ReleasePromotionResult:
    if existing.get("eval_run_id") != request.eval_run_id:
        raise AiEvalError("release_channel_already_promoted", "release channel already points at another eval run")
    return ReleasePromotionResult(
        release_id=str(existing["id"]),
        agent_version_id=request.agent_version_id,
        release_channel=request.target_release_channel,
        eval_run_id=request.eval_run_id,
        status=str(existing["status"]),
    )


def _promotion_result(request: ReleasePromotionRequest, release_id: str) -> ReleasePromotionResult:
    return ReleasePromotionResult(
        release_id=release_id,
        agent_version_id=request.agent_version_id,
        release_channel=request.target_release_channel,
        eval_run_id=request.eval_run_id,
        status="active",
    )


def _release_record(
    ctx: RequestContext, request: ReleasePromotionRequest, release_id: str, promoted_at: str
) -> AiAgentReleaseRecord:
    return AiAgentReleaseRecord(
        id=release_id,
        tenant_id=ctx.tenant_id,
        agent_version_id=request.agent_version_id,
        release_channel=request.target_release_channel,
        eval_run_id=request.eval_run_id,
        status="active",
        policy_version=request.policy_version,
        promoted_by=ctx.actor_user_id,
        promoted_at=promoted_at,
        created_at=promoted_at,
    )


def _require_same_case(existing: Mapping[str, object], case: EvalCaseInput) -> None:
    if existing.get("axis") != case.axis:
        raise AiEvalError("case_definition_conflict", "eval case axis changed without a suite version bump")
    if hash_json(json_object(existing.get("expected_json"))) != hash_json(case.expected_json):
        raise AiEvalError("case_definition_conflict", "eval case expected output changed without a suite version bump")


def _require_same_tuple(value: object, expected: tuple[str, ...], reason: str) -> None:
    if not isinstance(value, list | tuple) or tuple(value) != expected:
        raise AiEvalError(reason, "eval suite definition changed without a version bump")


def _require_supported_axis(axis: str) -> None:
    if axis not in _SUPPORTED_AXES:
        raise AiEvalError("unsupported_eval_axis", f"eval axis {axis} is not supported")


def _required_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise AiEvalError("missing_field", f"{field_name} is required")
