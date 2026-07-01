"""Application service helpers for eval runtime events workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.aip.eval_types import (
    EvalJsonObject,
    EvalRunRequest,
    ReleasePromotionRequest,
)
from foundry_lite.domain.context import RequestContext


class EvalRuntimeBoundary(Protocol):
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
    ) -> None: ...

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
    ) -> str | None: ...


def eval_run_completed_payload(
    request: EvalRunRequest,
    suite_id: str,
    status: str,
    is_passed: bool,
    summary: EvalJsonObject,
) -> dict[str, object]:
    return {
        "evalRunId": request.eval_run_id,
        "suiteId": suite_id,
        "suiteApiName": request.suite_api_name,
        "suiteVersion": request.suite_version,
        "agentVersionId": request.agent_version_id,
        "candidateReleaseChannel": request.candidate_release_channel,
        "status": status,
        "passed": is_passed,
        "score": summary["score"],
        "caseCount": summary["caseCount"],
        "failedCaseCount": summary["failedCaseCount"],
        "varianceFailures": summary["varianceFailures"],
        "missingRequiredAxes": summary["missingRequiredAxes"],
        "axes": summary["axes"],
    }


def release_promoted_payload(
    request: ReleasePromotionRequest,
    release_id: str,
) -> dict[str, object]:
    return {
        "releaseId": release_id,
        "agentVersionId": request.agent_version_id,
        "releaseChannel": request.target_release_channel,
        "evalRunId": request.eval_run_id,
        "policyVersion": request.policy_version,
        "status": "active",
    }
