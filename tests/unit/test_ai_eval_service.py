"""Unit tests for ``EvalService`` (AIP-lite P0k, §8.12/§15)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.aip.eval_service import (
    AiEvalError,
    EvalCaseInput,
    EvalRunRequest,
    EvalService,
    ReleasePromotionRequest,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied
from foundry_lite.infrastructure.repositories.ai_eval_repository import SqlAlchemyAiEvalRepository
from foundry_lite.infrastructure.schema import create_database
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_CTX = RequestContext(tenant_id="tenant-demo", actor_user_id="release-manager", roles=("admin",))
_VIEWER_CTX = RequestContext(tenant_id="tenant-demo", actor_user_id="viewer", roles=("viewer",))


def test_foundry_aip_facade_runs_eval_and_promotes_release(foundry: Any) -> None:
    result = foundry.aip.run_eval(
        eval_run_id="facade-eval-run-1",
        suite_api_name="order-copilot-facade-release",
        suite_version="v1",
        suite_description="Facade release smoke",
        agent_version_id="agent-v1",
        candidate_release_channel="stable",
        cases=(
            _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
            _case("action-requires-review", "action", {"execution": "proposal_only"}),
        ),
        ctx=_CTX,
        required_axes=("security", "action"),
    )
    release = foundry.aip.promote_agent_release(
        agent_version_id="agent-v1",
        target_release_channel="stable",
        eval_run_id=result.eval_run_id,
        ctx=_CTX,
    )

    assert result.is_passed is True
    assert release.status == "active"
    operations = foundry.operations.list_runs(ctx=demo_admin_context())
    audit_event_types = {str(event["event_type"]) for event in operations["auditEvents"]}
    outbox_event_types = {str(event["event_type"]) for event in operations["outboxEvents"]}
    assert "ai.eval_run.completed" in audit_event_types
    assert "ai.agent_release.promoted" in audit_event_types
    assert "ai.eval_run.completed" in outbox_event_types
    assert "ai.agent_release.promoted" in outbox_event_types


def test_ai_eval_records_results_and_allows_stable_release_with_required_axes() -> None:
    env = _eval_service()

    result = env.run_eval(
        _CTX,
        _request(
            cases=(
                _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            )
        ),
    )
    release = env.promote_release(
        _CTX,
        ReleasePromotionRequest(
            agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=result.eval_run_id
        ),
    )

    assert result.is_passed is True
    assert result.score == 1.0
    assert result.summary_json["axes"]["security"]["passed"] is True
    assert release.status == "active"
    assert release.release_channel == "stable"


def test_failed_eval_blocks_release_promotion() -> None:
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(cases=(_case("action-requires-review", "action", {"execution": "direct_action"}),)),
    )

    with pytest.raises(AiEvalError) as excinfo:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=result.eval_run_id
            ),
        )

    assert result.is_passed is False
    assert excinfo.value.reason == "eval_run_not_passed"


def test_stable_release_requires_security_and_action_axes() -> None:
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(cases=(_case("answer-cites-context", "answer", {"answer": "uses_citation"}),), required_axes=()),
    )

    with pytest.raises(AiEvalError) as excinfo:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=result.eval_run_id
            ),
        )

    assert result.is_passed is True
    assert excinfo.value.reason == "stable_requires_security_action_eval"


def test_repeated_case_variance_fails_closed() -> None:
    env = _eval_service()

    result = env.run_eval(
        _CTX,
        _request(
            eval_run_id="eval-run-variance",
            cases=(
                _case("same-prompt", "security", {"decision": "denied"}, sample_index=1),
                _case("same-prompt", "security", {"decision": "allowed"}, sample_index=2),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            ),
        ),
    )

    assert result.is_passed is False
    assert result.summary_json["varianceFailures"] == 1
    assert result.case_results[1].reason == "repeated_run_variance"


def test_eval_case_definition_changes_require_suite_version_bump() -> None:
    env = _eval_service()
    env.run_eval(_CTX, _request(cases=(_case("security-denies-cross-tenant", "security", {"decision": "denied"}),)))

    with pytest.raises(AiEvalError) as excinfo:
        env.run_eval(
            _CTX,
            _request(
                eval_run_id="eval-run-conflict",
                cases=(
                    EvalCaseInput(
                        case_api_name="security-denies-cross-tenant",
                        axis="security",
                        input_json={"prompt": "security-denies-cross-tenant"},
                        expected_json={"decision": "allowed"},
                        actual_json={"decision": "allowed"},
                    ),
                ),
            ),
        )

    assert excinfo.value.reason == "case_definition_conflict"


def test_canary_release_requires_matching_eval_channel() -> None:
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(candidate_release_channel="stable", cases=(_case("answer-cites-context", "answer", {"ok": True}),)),
    )

    with pytest.raises(AiEvalError) as excinfo:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="canary", eval_run_id=result.eval_run_id
            ),
        )

    assert excinfo.value.reason == "release_channel_mismatch"


def test_ai_eval_rejects_invalid_request_shapes() -> None:
    env = _eval_service()
    cases = (_case("security-denies-cross-tenant", "security", {"decision": "denied"}),)

    invalid_requests = (
        (_request(candidate_release_channel="prod", cases=cases), "unsupported_release_channel"),
        (_request(cases=cases, min_score=1.5), "invalid_min_score"),
        (_request(cases=()), "empty_eval_suite"),
        (_request(cases=cases, required_axes=("unknown",)), "unsupported_eval_axis"),
        (
            _request(cases=(_case("bad-sample", "security", {"decision": "denied"}, sample_index=0),)),
            "invalid_sample_index",
        ),
        (_request(cases=(_case("bad-weight", "security", {"decision": "denied"}, weight=-1.0),)), "invalid_weight"),
        (_request(cases=(_case("zero-weight", "security", {"decision": "denied"}, weight=0.0),)), "invalid_weight"),
    )

    for request, reason in invalid_requests:
        with pytest.raises(AiEvalError) as excinfo:
            env.run_eval(_CTX, request)
        assert excinfo.value.reason == reason


def test_ai_eval_supports_list_expected_values() -> None:
    env = _eval_service()

    result = env.run_eval(
        _CTX,
        _request(
            eval_run_id="eval-run-list-expected",
            cases=(
                _case(
                    "answer-list-output",
                    "answer",
                    {"items": ["context", "citation"]},
                    expected_json={"items": ["context", "citation"]},
                ),
            ),
            required_axes=(),
        ),
    )

    assert result.is_passed is True


def test_ai_eval_nested_mapping_mismatch_fails_closed() -> None:
    env = _eval_service()

    result = env.run_eval(
        _CTX,
        _request(
            eval_run_id="eval-run-nested-mismatch",
            cases=(
                _case(
                    "answer-nested-output",
                    "answer",
                    {"answer": "flat"},
                    expected_json={"answer": {"citation": "ctx-1"}},
                ),
            ),
            required_axes=(),
        ),
    )

    assert result.is_passed is False
    assert result.case_results[0].reason == "expected_output_mismatch"


def test_promote_release_rejects_invalid_or_unknown_runs() -> None:
    env = _eval_service()

    with pytest.raises(AiEvalError) as bad_channel:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="prod", eval_run_id="missing-eval-run"
            ),
        )
    with pytest.raises(AiEvalError) as missing:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="stable", eval_run_id="missing-eval-run"
            ),
        )

    assert bad_channel.value.reason == "unsupported_release_channel"
    assert missing.value.reason == "eval_run_not_found"


def test_promote_release_rejects_agent_version_mismatch() -> None:
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(cases=(_case("answer-cites-context", "answer", {"answer": "uses_citation"}),), required_axes=()),
    )

    with pytest.raises(AiEvalError) as excinfo:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v2", target_release_channel="stable", eval_run_id=result.eval_run_id
            ),
        )

    assert excinfo.value.reason == "agent_version_mismatch"


def test_promote_release_conflicts_when_channel_already_points_elsewhere() -> None:
    env = _eval_service()
    first = env.run_eval(
        _CTX,
        _request(
            cases=(
                _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            )
        ),
    )
    second = env.run_eval(
        _CTX,
        _request(
            eval_run_id="eval-run-2",
            cases=(
                _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            ),
        ),
    )
    env.promote_release(
        _CTX,
        ReleasePromotionRequest(
            agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=first.eval_run_id
        ),
    )

    with pytest.raises(AiEvalError) as excinfo:
        env.promote_release(
            _CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=second.eval_run_id
            ),
        )

    assert excinfo.value.reason == "release_channel_already_promoted"


def test_canary_release_skips_stable_axis_requirements() -> None:
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(
            candidate_release_channel="canary",
            cases=(_case("answer-cites-context", "answer", {"answer": "uses_citation"}),),
            required_axes=(),
        ),
    )

    release = env.promote_release(
        _CTX,
        ReleasePromotionRequest(
            agent_version_id="agent-v1", target_release_channel="canary", eval_run_id=result.eval_run_id
        ),
    )

    assert release.status == "active"


def test_ai_eval_suite_axis_changes_require_version_bump() -> None:
    env = _eval_service()
    env.run_eval(_CTX, _request(cases=(_case("security-denies-cross-tenant", "security", {"decision": "denied"}),)))

    with pytest.raises(AiEvalError) as excinfo:
        env.run_eval(
            _CTX,
            _request(
                eval_run_id="eval-run-suite-conflict",
                cases=(_case("action-requires-review", "action", {"execution": "proposal_only"}),),
            ),
        )

    assert excinfo.value.reason == "suite_definition_conflict"


def test_ai_eval_case_axis_changes_require_version_bump() -> None:
    env = _eval_service()
    env.run_eval(
        _CTX,
        _request(
            cases=(
                _case("shared-case", "security", {"decision": "denied"}),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            )
        ),
    )

    with pytest.raises(AiEvalError) as excinfo:
        env.run_eval(
            _CTX,
            _request(
                eval_run_id="eval-run-case-axis-conflict",
                cases=(
                    _case("shared-case", "action", {"execution": "proposal_only"}),
                    _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
                ),
            ),
        )

    assert excinfo.value.reason == "case_definition_conflict"


def test_run_eval_requires_permission() -> None:
    env = _eval_service()

    with pytest.raises(PermissionDenied) as excinfo:
        env.run_eval(
            _VIEWER_CTX,
            _request(cases=(_case("security-denies-cross-tenant", "security", {"decision": "denied"}),)),
        )

    assert excinfo.value.details["permission"] == "aip:evals:run"


def test_promote_release_requires_permission() -> None:
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(
            cases=(
                _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            )
        ),
    )

    with pytest.raises(PermissionDenied) as excinfo:
        env.promote_release(
            _VIEWER_CTX,
            ReleasePromotionRequest(
                agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=result.eval_run_id
            ),
        )

    assert excinfo.value.details["permission"] == "aip:releases:promote"


def test_run_eval_conflicts_when_completion_matches_no_running_row() -> None:
    """A CAS miss on completion must fail closed with a conflict, not silently succeed."""
    env = _eval_service()
    env.ai_eval_repository = _NoCompletionRepository(env.ai_eval_repository)

    with pytest.raises(ConflictDetected):
        env.run_eval(
            _CTX,
            _request(cases=(_case("security-denies-cross-tenant", "security", {"decision": "denied"}),)),
        )


class _NoCompletionRepository:
    """Delegates to a real repository but simulates a lost completion CAS race."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def complete_run(self, **kwargs: Any) -> Any:
        return None


def test_promote_release_reconciles_concurrent_first_promotion() -> None:
    """A racing first-time promotion hits the unique constraint but resolves idempotently, not a 500."""
    env = _eval_service()
    result = env.run_eval(
        _CTX,
        _request(
            cases=(
                _case("security-denies-cross-tenant", "security", {"decision": "denied"}),
                _case("action-requires-review", "action", {"execution": "proposal_only"}),
            )
        ),
    )
    request = ReleasePromotionRequest(
        agent_version_id="agent-v1", target_release_channel="stable", eval_run_id=result.eval_run_id
    )
    env.ai_eval_repository = _RaceOnceRepository(env.ai_eval_repository, request)

    release = env.promote_release(_CTX, request)

    assert release.status == "active"
    assert release.eval_run_id == result.eval_run_id


class _RaceOnceRepository:
    """Delegates to a real repository but hides the release on the first pre-check.

    This forces ``promote_release`` past the existence guard and into the unique
    constraint, exercising the idempotent reconcile path deterministically.
    """

    def __init__(self, delegate: Any, racing_request: ReleasePromotionRequest) -> None:
        self._delegate = delegate
        self._racing_request = racing_request
        self._hidden = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def release_by_agent_channel(self, **kwargs: Any) -> Any:
        if self._hidden:
            self._hidden = False
            self._delegate.create_release(
                transaction=kwargs["transaction"],
                record=_racing_release_record(self._racing_request, kwargs),
            )
            return None
        return self._delegate.release_by_agent_channel(**kwargs)


def _racing_release_record(request: ReleasePromotionRequest, kwargs: Mapping[str, Any]) -> Any:
    from foundry_lite.application.ports.ai_eval_repository import AiAgentReleaseRecord
    from foundry_lite.application.services.aip.eval_identifiers import ai_agent_release_id

    tenant_id = str(kwargs["tenant_id"])
    release_id = ai_agent_release_id(tenant_id, request.agent_version_id, request.target_release_channel)
    return AiAgentReleaseRecord(
        id=release_id,
        tenant_id=tenant_id,
        agent_version_id=request.agent_version_id,
        release_channel=request.target_release_channel,
        eval_run_id=request.eval_run_id,
        status="active",
        policy_version=request.policy_version,
        promoted_by="racing-winner",
        promoted_at="2026-07-02T00:00:00Z",
        created_at="2026-07-02T00:00:00Z",
    )


def _eval_service() -> EvalService:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_database(engine)
    service = EvalService(
        engine=engine,
        ai_eval_repository=SqlAlchemyAiEvalRepository(engine),
        policy=PolicyService(allow_unwired_classification_provider=True),
    )
    service.bind_collaborators({"runtime_service": _RecordingRuntime()})
    return service


class _RecordingRuntime:
    def __init__(self) -> None:
        self.audit_events: list[dict[str, object]] = []
        self.outbox_events: list[dict[str, object]] = []

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        del conn, ctx, policy_decision, before_ref, correlation_id
        self.audit_events.append(
            {
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
                "decision": decision,
                "after_ref": dict(after_ref or {}),
            }
        )

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None:
        del conn, ctx, idempotency_key, correlation_id
        self.outbox_events.append(
            {
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "payload": dict(payload),
            }
        )
        return "outbox-test"


def _request(
    *,
    eval_run_id: str = "eval-run-1",
    suite_api_name: str = "order-copilot-release",
    suite_version: str = "v1",
    agent_version_id: str = "agent-v1",
    candidate_release_channel: str = "stable",
    cases: tuple[EvalCaseInput, ...],
    min_score: float = 1.0,
    required_axes: tuple[str, ...] = ("security", "action"),
) -> EvalRunRequest:
    return EvalRunRequest(
        eval_run_id=eval_run_id,
        suite_api_name=suite_api_name,
        suite_version=suite_version,
        suite_description="Order Copilot release suite",
        agent_version_id=agent_version_id,
        candidate_release_channel=candidate_release_channel,
        cases=cases,
        min_score=min_score,
        required_axes=required_axes,
    )


def _case(
    case_api_name: str,
    axis: str,
    actual_json: dict[str, object],
    *,
    expected_json: dict[str, object] | None = None,
    sample_index: int = 1,
    weight: float = 1.0,
) -> EvalCaseInput:
    expected = expected_json or _expected_for_axis(axis)
    return EvalCaseInput(
        case_api_name=case_api_name,
        axis=axis,
        input_json={"prompt": case_api_name},
        expected_json=expected,
        actual_json=actual_json,
        rubric_json={"metric": "exact_subset_v1"},
        sample_index=sample_index,
        weight=weight,
    )


def _expected_for_axis(axis: str) -> dict[str, object]:
    if axis == "security":
        return {"decision": "denied"}
    if axis == "action":
        return {"execution": "proposal_only"}
    if axis == "answer":
        return {"answer": "uses_citation"}
    return {"ok": True}
