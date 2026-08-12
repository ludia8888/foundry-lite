"""Pure fingerprints and safe projections for external release delivery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentObservation,
)
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.ports.source_control_release import (
    PullRequestSnapshot,
    PullRequestTarget,
    RequiredCheckEvidence,
    SourceControlMergeReceipt,
    SourceRepositoryRef,
)
from foundry_lite.application.services.aip.external_release_delivery_state import (
    application_delivery_summary,
    delivery_receipt_status,
)


def request_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def source_target_ref(target: PullRequestTarget) -> dict[str, object]:
    return {
        "repositoryId": target.repository.repository_id,
        "repositoryOwner": target.repository.owner,
        "repositoryName": target.repository.name,
        "pullNumber": target.pull_number,
        "baseRef": target.expected_base_ref,
        "headSha": target.expected_head_sha,
    }


def source_candidate_ref(snapshot: PullRequestSnapshot) -> dict[str, object]:
    payload: dict[str, object] = {
        "headRef": snapshot.head_ref,
        "baseSha": snapshot.base_sha,
        "headSha": snapshot.target.expected_head_sha,
        "testMergeCommitSha": snapshot.test_merge_commit_sha,
        "checksCommitSha": snapshot.checks_commit_sha,
        "rulesFingerprint": snapshot.rules_fingerprint,
        "checksFingerprint": snapshot.checks_fingerprint,
        "requiredApprovalCount": snapshot.required_approval_count,
        "approvalCount": len(snapshot.approvals),
        "requiredChecks": [_check_projection(check) for check in snapshot.required_checks],
        "blockingReasons": list(snapshot.blocking_reasons),
        "providerRequestId": snapshot.provider_request_id,
        "isReadyToMerge": snapshot.is_ready_to_merge,
    }
    binding = snapshot.target.candidate_binding
    if binding is not None:
        payload.update(
            {
                "candidateTreeSha": binding.expected_tree_sha,
                "manifestArtifactPath": binding.manifest.artifact_path,
                "manifestFingerprint": binding.manifest.manifest_fingerprint,
            }
        )
    return payload


def source_snapshot_evidence(snapshot: PullRequestSnapshot, *, is_required: bool) -> dict[str, object]:
    candidate = source_candidate_ref(snapshot)
    checks_passed = all(check.is_successful for check in snapshot.required_checks)
    status = "merged" if snapshot.is_merged else "ready" if snapshot.is_ready_to_merge else "blocked"
    return {
        "provider": snapshot.target.repository.provider,
        "isConfigured": True,
        "isRequired": is_required,
        "status": status,
        "target": source_target_ref(snapshot.target),
        "candidate": candidate,
        "ciReceipt": _ci_receipt(snapshot, checks_passed),
    }


def source_receipt_ref(receipt: SourceControlMergeReceipt) -> dict[str, object]:
    return {
        "status": receipt.status.value,
        "repositoryId": receipt.repository_id,
        "pullNumber": receipt.pull_number,
        "headSha": receipt.head_sha,
        "mergeCommitSha": receipt.merge_commit_sha,
        "mergedAt": receipt.merged_at,
        "providerRequestId": receipt.provider_request_id,
        "evidence": dict(receipt.evidence),
    }


def source_target_from_ref(ref: Mapping[str, object], provider: str) -> PullRequestTarget:
    repository = SourceRepositoryRef(
        provider=provider,
        repository_id=_integer(ref, "repositoryId"),
        owner=_text(ref, "repositoryOwner"),
        name=_text(ref, "repositoryName"),
    )
    return PullRequestTarget(
        repository=repository,
        pull_number=_integer(ref, "pullNumber"),
        expected_base_ref=_text(ref, "baseRef"),
        expected_head_sha=_text(ref, "headSha"),
    )


def deployment_observation_ref(observation: InfrastructureDeploymentObservation) -> dict[str, object]:
    return {
        "provider": observation.provider,
        "serviceId": observation.service_id,
        "deployId": observation.deploy_id,
        "status": observation.status,
        "providerStatus": observation.provider_status,
        "commitId": observation.commit_id,
        "trigger": observation.trigger,
        "createdAt": _timestamp(observation.created_at),
        "startedAt": _timestamp(observation.started_at),
        "updatedAt": _timestamp(observation.updated_at),
        "finishedAt": _timestamp(observation.finished_at),
        "isTerminal": observation.is_terminal,
        "isSuccessful": observation.is_successful,
        "providerRequestId": observation.provider_request_id,
    }


def delivery_projection(row: ReleaseDeliveryRecord) -> dict[str, object]:
    return {
        "deliveryId": row.delivery_id,
        "applicationId": row.application_id,
        "proposalId": row.proposal_id,
        "releaseKind": row.release_kind,
        "workflowRunId": row.workflow_run_id,
        "parentDeliveryId": row.parent_delivery_id,
        "provider": row.provider,
        "operation": row.operation,
        "environment": row.environment,
        "status": row.status,
        "ledgerStatus": row.status,
        "receiptStatus": delivery_receipt_status(row),
        "isOperationallyComplete": row.operation == "source_merge" and row.status == "landed",
        "target": dict(row.target_ref),
        "candidate": dict(row.candidate_ref) if row.candidate_ref is not None else None,
        "providerOperationId": row.provider_operation_id,
        "providerResourceId": row.provider_resource_id,
        "priorResourceId": row.prior_resource_id,
        "result": dict(row.result_ref) if row.result_ref is not None else None,
        "error": dict(row.error_ref) if row.error_ref is not None else None,
        "requestId": row.request_id,
        "createdBy": row.created_by,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
        "dispatchStartedAt": row.dispatch_started_at,
        "completedAt": row.completed_at,
    }


def source_delivery_evidence(row: ReleaseDeliveryRecord, *, is_required: bool) -> dict[str, object]:
    ci_receipt = _stored_ci_receipt(row)
    return {
        "provider": row.provider,
        "isConfigured": True,
        "isRequired": is_required,
        "status": row.status,
        "target": dict(row.target_ref),
        "candidate": dict(row.candidate_ref or {}),
        "ciReceipt": ci_receipt,
        "delivery": delivery_projection(row),
    }


def latest_rows(rows: Sequence[ReleaseDeliveryRecord], operation: str) -> list[ReleaseDeliveryRecord]:
    matching = [row for row in rows if row.operation == operation]
    return sorted(matching, key=lambda row: (row.created_at, row.delivery_id), reverse=True)


def _ci_receipt(snapshot: PullRequestSnapshot, is_checks_passed: bool) -> dict[str, object]:
    return {
        "id": "external-ci-receipt",
        "label": "Candidate-scoped external CI receipt",
        "status": "passed" if is_checks_passed else "blocked",
        "proofKind": "github_merge_result_or_head_required_checks",
        "details": {
            "headSha": snapshot.target.expected_head_sha,
            "testMergeCommitSha": snapshot.test_merge_commit_sha,
            "checksCommitSha": snapshot.checks_commit_sha,
            "checksFingerprint": snapshot.checks_fingerprint,
            "rulesFingerprint": snapshot.rules_fingerprint,
            "requiredCheckCount": len(snapshot.required_checks),
            "allRequiredChecksSuccessful": is_checks_passed,
        },
    }


def _stored_ci_receipt(row: ReleaseDeliveryRecord) -> dict[str, object]:
    candidate = row.candidate_ref or {}
    checks = candidate.get("requiredChecks")
    rows = checks if isinstance(checks, list) else []
    is_passed = candidate.get("isReadyToMerge") is True and all(
        isinstance(item, Mapping) and item.get("isSuccessful") is True for item in rows
    )
    return {
        "id": "external-ci-receipt",
        "label": "Candidate-scoped external CI receipt",
        "status": "passed" if is_passed else "blocked",
        "proofKind": "github_merge_result_or_head_required_checks",
        "details": {
            "headSha": candidate.get("headSha"),
            "testMergeCommitSha": candidate.get("testMergeCommitSha"),
            "checksCommitSha": candidate.get("checksCommitSha"),
            "checksFingerprint": candidate.get("checksFingerprint"),
            "rulesFingerprint": candidate.get("rulesFingerprint"),
            "requiredCheckCount": len(rows),
            "allRequiredChecksSuccessful": is_passed,
        },
    }


def _check_projection(check: RequiredCheckEvidence) -> dict[str, object]:
    return {
        "context": check.context,
        "commitSha": check.commit_sha,
        "status": check.status,
        "conclusion": check.conclusion,
        "source": check.source,
        "isSuccessful": check.is_successful,
    }


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"stored release delivery {key} is invalid")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"stored release delivery {key} is invalid")
    return value


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "application_delivery_summary",
    "delivery_projection",
    "deployment_observation_ref",
    "latest_rows",
    "request_fingerprint",
    "source_candidate_ref",
    "source_delivery_evidence",
    "source_receipt_ref",
    "source_snapshot_evidence",
    "source_target_from_ref",
    "source_target_ref",
]
