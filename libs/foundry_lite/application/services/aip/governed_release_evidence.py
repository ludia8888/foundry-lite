"""Pure release evidence, target verification, and replay receipt checks."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]


def release_candidate(
    kind: str,
    proposal: JsonObject,
    ctx: RequestContext,
) -> dict[str, object]:
    candidate = dict(proposal)
    policy = candidate.get("reviewPolicy")
    is_separate_reviewer_required = isinstance(policy, Mapping) and policy.get("requiresSeparateReviewer") is True
    if not isinstance(policy, Mapping):
        candidate["reviewPolicy"] = {
            "requiresAssignment": True,
            "requiresSeparateReviewer": is_separate_reviewer_required,
            "blocksStaleProposal": True,
        }
    assignee, author = _review_coordinates(kind, candidate)
    can_review = _can_review(author, ctx.actor_user_id, is_separate_reviewer_required)
    candidate["canCurrentUserReview"] = assignee == ctx.actor_user_id and can_review
    candidate["canCurrentUserClaim"] = assignee is None and can_review
    return candidate


def _review_coordinates(kind: str, candidate: JsonObject) -> tuple[object, object]:
    if kind == "ontology":
        return candidate.get("assigneeUserId"), candidate.get("submittedByUserId")
    return candidate.get("assignedTo"), candidate.get("createdBy")


def _can_review(author: object, actor_user_id: str, is_separate_reviewer_required: bool) -> bool:
    return not is_separate_reviewer_required or author != actor_user_id


def require_proposal_identity(proposal: JsonObject, proposal_id: str) -> None:
    if proposal.get("id") != proposal_id:
        raise ConflictDetected(
            "release proposal identity changed while loading the candidate",
            details={"proposalId": proposal_id},
        )


def verified_pipeline_deploy_target(
    arguments: JsonObject,
    proposal: JsonObject,
    evidence: JsonObject,
) -> tuple[str, str]:
    if release_stage("pipeline", proposal, evidence) != "merged":
        raise ConflictDetected("pipeline proposal is not ready for deployment")
    merged = _required_mapping(evidence, "mergedVersion")
    pipeline_id = _required_text(evidence, "pipelineId")
    version_id = _required_text(merged, "id")
    _require_exact_text(arguments, "pipelineId", pipeline_id)
    _require_exact_text(arguments, "versionId", version_id)
    return pipeline_id, version_id


def verified_rollback_target(arguments: JsonObject, view: JsonObject) -> Mapping[str, object]:
    target = view.get("rollbackTarget")
    allowed_stages = {"active", "deployed"}
    if view.get("releaseKind") == "pipeline":
        allowed_stages.add("deployment_failed")
    if view.get("stage") not in allowed_stages or not isinstance(target, Mapping):
        raise ConflictDetected("release has no current safe rollback target")
    keys = (
        ("targetVersionNumber", "expectedActiveVersionNumber")
        if view.get("releaseKind") == "ontology"
        else ("pipelineId", "targetVersionId", "rolledBackFromId")
    )
    for key in keys:
        if arguments.get(key) != target.get(key):
            raise ConflictDetected(
                "rollback target does not match the current server evidence",
                details={"field": key, "expected": target.get(key)},
            )
    return target


def pipeline_deploy_options(evidence: JsonObject) -> dict[str, object]:
    current = evidence.get("currentDeployment")
    current_id = current.get("id") if isinstance(current, Mapping) else None
    return {"expectedCurrentDeploymentId": current_id if isinstance(current_id, str) else "none"}


def pipeline_release_evidence(
    pipeline_id: str,
    proposal: JsonObject,
    versions: list[Mapping[str, object]],
    deployments: list[Mapping[str, object]],
) -> dict[str, object]:
    version = _matching_row(versions, "proposalId", proposal.get("id"))
    deployment = _matching_row(deployments, "versionId", _mapping_value(version, "id"))
    current = _current_pipeline_deployment(deployments)
    return {
        "pipelineId": pipeline_id,
        "changeDiff": _mapping_copy(proposal.get("changeDiff")),
        "testReceipt": _mapping_copy(proposal.get("testReceipt")),
        "diffCompleteness": proposal.get("diffCompleteness"),
        "mergedVersion": _mapping_copy(version),
        "candidateDeployment": _mapping_copy(deployment),
        "currentDeployment": _mapping_copy(current),
        "rollbackTarget": _pipeline_rollback_target(pipeline_id, current, deployments),
    }


def _matching_row(
    rows: list[Mapping[str, object]],
    key: str,
    expected: object,
) -> Mapping[str, object] | None:
    return next((row for row in rows if row.get(key) == expected), None)


def _mapping_value(row: Mapping[str, object] | None, key: str) -> object:
    return row.get(key) if isinstance(row, Mapping) else None


def _mapping_copy(row: object) -> dict[str, object] | None:
    return dict(row) if isinstance(row, Mapping) else None


def _current_pipeline_deployment(
    deployments: list[Mapping[str, object]],
) -> Mapping[str, object] | None:
    if not deployments or deployments[0].get("status") != "PROMOTED":
        return None
    return deployments[0]


def ontology_release_evidence(active: JsonObject, proposal: JsonObject) -> dict[str, object]:
    applied = proposal.get("appliedOntologyVersion")
    applied_version = applied.get("versionNumber") if isinstance(applied, Mapping) else None
    active_version = active.get("versionNumber")
    is_current = isinstance(applied_version, int) and applied_version == active_version
    rollback_target = (
        {"targetVersionNumber": active_version - 1, "expectedActiveVersionNumber": active_version}
        if is_current and isinstance(active_version, int) and active_version > 1
        else None
    )
    return {
        "activeOntology": dict(active),
        "isAppliedVersionCurrent": is_current,
        "rollbackTarget": rollback_target,
    }


def release_stage(kind: str, proposal: JsonObject, evidence: JsonObject) -> str:
    status = str(proposal.get("status", "unknown"))
    if kind == "ontology":
        if status == "applied" and evidence.get("isAppliedVersionCurrent") is True:
            return "active"
        return "superseded" if status == "applied" else _proposal_stage(status)
    candidate = evidence.get("candidateDeployment")
    current = evidence.get("currentDeployment")
    if _is_current_candidate(candidate, current):
        return _external_application_stage(evidence)
    if isinstance(candidate, Mapping):
        return "superseded"
    if evidence.get("mergedVersion") is not None or status == "executed":
        return "merged"
    return _proposal_stage(status)


def _external_application_stage(evidence: JsonObject) -> str:
    external = evidence.get("externalDelivery")
    if not isinstance(external, Mapping) or external.get("isConfigured") is not True:
        return "deployed"
    application = external.get("applicationStatus")
    if not isinstance(application, Mapping):
        return "deployment_unverified"
    status = application.get("status")
    if status == "live" and application.get("isOperationallyComplete") is True:
        return "deployed"
    if status in {"failed", "not_delivered"}:
        return "deployment_failed"
    if status in {"outcome_unknown", "observation_unavailable"}:
        return "deployment_unverified"
    return "deploying"


def next_actions(kind: str, stage: str, evidence: JsonObject) -> list[str]:
    if stage in {"awaiting_assignment", "awaiting_review"}:
        return _awaiting_review_actions(evidence)
    if stage == "approved":
        return _allowed_action("execute_approved_release", _source_candidate_is_published(evidence))
    return _delivery_actions(kind, stage, evidence)


def _awaiting_review_actions(evidence: JsonObject) -> list[str]:
    if _source_candidate_needs_publication(evidence):
        return _allowed_action("publish_release_candidate", evidence.get("canCurrentUserPublish") is True)
    if evidence.get("canCurrentUserReview") is True:
        return ["submit_release_decision"]
    return _allowed_action("assign_release_reviewer", evidence.get("canCurrentUserClaim") is True)


def _allowed_action(action: str, is_allowed: bool) -> list[str]:
    return [action] if is_allowed else []


def _delivery_actions(kind: str, stage: str, evidence: JsonObject) -> list[str]:
    if kind == "pipeline" and stage == "merged":
        return ["deploy_release"]
    if kind == "pipeline" and stage == "deployment_failed":
        return _allowed_action("rollback_release", evidence.get("rollbackTarget") is not None)
    if stage not in {"active", "deployed"} or evidence.get("rollbackTarget") is None:
        return []
    return _allowed_action("rollback_release", _has_external_rollback_target(kind, evidence))


def _source_candidate_needs_publication(evidence: JsonObject) -> bool:
    external = evidence.get("externalSourceControl")
    return isinstance(external, Mapping) and external.get("status") == "not_published"


def _source_candidate_is_published(evidence: JsonObject) -> bool:
    external = evidence.get("externalSourceControl")
    if external is None:
        return True
    if not isinstance(external, Mapping):
        return False
    if external.get("isConfigured") is not True:
        return external.get("isRequired") is not True
    publication = external.get("publication")
    return isinstance(publication, Mapping) and publication.get("receiptStatus") == "published"


def _has_external_rollback_target(kind: str, evidence: JsonObject) -> bool:
    external = evidence.get("externalDelivery")
    if kind != "pipeline" or not isinstance(external, Mapping) or external.get("isConfigured") is not True:
        return True
    target = external.get("applicationRollbackTarget")
    return isinstance(target, Mapping) and all(
        isinstance(target.get(field), str) and bool(str(target[field]).strip())
        for field in ("targetDeployId", "targetCommitId", "rolledBackFromDeployId")
    )


def require_deployment_replay(
    arguments: JsonObject,
    deployment: JsonObject,
    evidence: JsonObject,
    *,
    is_rollback: bool,
) -> None:
    _require_deployment_identity(arguments, deployment, is_rollback=is_rollback)
    merged = _required_mapping(evidence, "mergedVersion")
    if not is_rollback:
        if merged.get("id") != _required_text(arguments, "versionId"):
            raise ConflictDetected("deployment receipt is not tied to the exact merged proposal")
        return
    candidate = _required_mapping(evidence, "candidateDeployment")
    if not _valid_rollback_replay(arguments, deployment, merged, candidate):
        raise ConflictDetected("rollback receipt is not tied to the exact merged proposal")


def required_mapping(arguments: JsonObject, key: str) -> Mapping[str, object]:
    return _required_mapping(arguments, key)


def _require_deployment_identity(
    arguments: JsonObject,
    deployment: JsonObject,
    *,
    is_rollback: bool,
) -> None:
    version_field = "targetVersionId" if is_rollback else "versionId"
    expected = {
        "pipelineId": _required_text(arguments, "pipelineId"),
        "versionId": _required_text(arguments, version_field),
        "idempotencyKey": _required_text(arguments, "idempotencyKey"),
    }
    if any(deployment.get(key) != value for key, value in expected.items()):
        raise ConflictDetected("deployment receipt does not match the governed release request")
    if is_rollback and deployment.get("rolledBackFromId") != _required_text(arguments, "rolledBackFromId"):
        raise ConflictDetected("rollback receipt does not match the governed release request")


def _valid_rollback_replay(
    arguments: JsonObject,
    deployment: JsonObject,
    merged: JsonObject,
    candidate: JsonObject,
) -> bool:
    return (
        candidate.get("id") == _required_text(arguments, "rolledBackFromId")
        and candidate.get("versionId") == merged.get("id")
        and deployment.get("versionId") == _required_text(arguments, "targetVersionId")
    )


def _required_text(arguments: JsonObject, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required")
    return value.strip()


def _required_mapping(arguments: JsonObject, key: str) -> Mapping[str, object]:
    value = arguments.get(key)
    if not isinstance(value, Mapping):
        raise ConflictDetected("durable deployment receipt is malformed", details={"field": key})
    return value


def _require_exact_text(arguments: JsonObject, key: str, expected: str) -> None:
    if _required_text(arguments, key) != expected:
        raise ConflictDetected(
            "deployment target does not match the approved merged proposal",
            details={"field": key, "expected": expected},
        )


def _proposal_stage(status: str) -> str:
    if status in {"submitted", "in_review"}:
        return "awaiting_review"
    return "approved" if status == "approved" else status


def _is_current_candidate(candidate: object, current: object) -> bool:
    return (
        isinstance(candidate, Mapping)
        and isinstance(current, Mapping)
        and candidate.get("id") == current.get("id")
        and candidate.get("status") == "PROMOTED"
    )


def _pipeline_rollback_target(
    pipeline_id: str,
    current: Mapping[str, object] | None,
    deployments: list[Mapping[str, object]],
) -> dict[str, object] | None:
    if current is None or len(deployments) < 2:
        return None
    target_version_id = deployments[1].get("versionId")
    current_id = current.get("id")
    if not isinstance(target_version_id, str) or not isinstance(current_id, str):
        return None
    return {
        "pipelineId": pipeline_id,
        "targetVersionId": target_version_id,
        "rolledBackFromId": current_id,
    }


__all__ = [
    "next_actions",
    "ontology_release_evidence",
    "pipeline_deploy_options",
    "pipeline_release_evidence",
    "release_candidate",
    "release_stage",
    "require_deployment_replay",
    "require_proposal_identity",
    "required_mapping",
    "verified_pipeline_deploy_target",
    "verified_rollback_target",
]
