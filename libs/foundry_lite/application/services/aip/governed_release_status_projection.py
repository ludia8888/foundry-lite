"""Pure operational-completion and recovery projections for release status."""

from __future__ import annotations

from collections.abc import Mapping

JsonObject = Mapping[str, object]


def operational_completion(view: JsonObject) -> dict[str, object]:
    """Distinguish normal serving completion from the rollback rehearsal proof."""

    stage = view.get("stage")
    return {
        "completionPurpose": "normal_operational_release",
        "isComplete": stage in {"active", "deployed"},
        "stage": stage,
        "isRollbackRehearsal": False,
    }


def unavailable_completion_coordinates(reason: str) -> dict[str, object]:
    """Return a non-actionable rollback-rehearsal coordinate projection."""

    return {
        "attestationPurpose": "rollback_rehearsal",
        "ontologyWorkflowRunId": None,
        "pipelineWorkflowRunId": None,
        "isEligible": False,
        "nextAction": None,
        "reason": reason,
    }


def external_delivery_recovery_evidence(value: JsonObject) -> dict[str, object]:
    """Hide an unsafe provider target when the new candidate never became live."""

    projected = dict(value)
    if is_failed_application_candidate(value):
        projected["applicationRollbackTarget"] = None
        projected["recoveryMode"] = "internal_only"
        projected["recoveryReason"] = "failed_candidate_never_became_live_existing_deploy_preserved"
        return projected
    target = value.get("applicationRollbackTarget")
    projected["recoveryMode"] = "provider_rollback" if isinstance(target, Mapping) else "unavailable"
    return projected


def is_failed_application_candidate(delivery: JsonObject) -> bool:
    application = delivery.get("applicationStatus")
    return isinstance(application, Mapping) and application.get("status") in {"failed", "not_delivered"}


__all__ = [
    "external_delivery_recovery_evidence",
    "is_failed_application_candidate",
    "operational_completion",
    "unavailable_completion_coordinates",
]
