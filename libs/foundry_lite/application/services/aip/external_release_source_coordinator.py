"""Durable source-control merge dispatch and read-only reconciliation."""

from __future__ import annotations

from typing import NoReturn

from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import InfrastructureDeploymentAdapter
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryRecord,
    ReleaseDeliveryTerminalStatus,
)
from foundry_lite.application.ports.source_control_release import (
    PullRequestTarget,
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReleasePort,
)
from foundry_lite.application.services.aip.external_release_delivery_ledger import ExternalReleaseDeliveryLedger
from foundry_lite.application.services.aip.external_release_delivery_payloads import (
    delivery_projection,
    source_receipt_ref,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    ExternalReleaseDeliveryOutcomeUnknown,
    receipt_matches_delivery,
    required_candidate_text,
)
from foundry_lite.application.services.aip.external_release_source_preflight import (
    ManualDeploymentPolicyFailure,
    require_manual_deployment_policy,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

_KNOWN_NOT_COMMITTED_KINDS = frozenset(
    {"validation", "authentication", "authorization", "rate_limited", "conflict", "unsupported", "not_found"}
)


class ExternalReleaseSourceCoordinator:
    """Advance one write-ahead source intent without blind mutation replay."""

    def __init__(
        self,
        source_adapter: SourceControlReleasePort,
        infrastructure_adapter: InfrastructureDeploymentAdapter,
        config: GovernedReleaseDeliveryConfig,
        ledger: ExternalReleaseDeliveryLedger,
        runtime_service: RuntimeEvidenceBoundary,
    ) -> None:
        self._source = source_adapter
        self._infrastructure = infrastructure_adapter
        self._config = config
        self._ledger = ledger
        self._runtime = runtime_service

    def resume(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        target: PullRequestTarget | None,
    ) -> dict[str, object]:
        if row.status == "landed":
            return delivery_projection(row)
        if target is None or not _target_matches_row(target, row):
            raise ConflictDetected("source merge requires the exact freshly verified publication target")
        if row.status in {"dispatching", "ambiguous"}:
            return delivery_projection(self._reconcile(ctx, row, target))
        if row.status in {"failed", "absent"}:
            raise ConflictDetected("source merge previously reached a known non-success outcome")
        self._require_policy(ctx, row)
        claimed = self._ledger.claim(ctx, row)
        if claimed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("source merge dispatch fence was lost")
        return delivery_projection(self._dispatch(ctx, claimed, target))

    def _dispatch(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        target: PullRequestTarget,
    ) -> ReleaseDeliveryRecord:
        request = SourceControlMergeRequest(
            target,
            self._config.source_merge_method,
            row.idempotency_key,
            required_candidate_text(row, "baseSha"),
            required_candidate_text(row, "rulesFingerprint"),
            required_candidate_text(row, "checksFingerprint"),
        )
        try:
            receipt = self._source.merge_pull_request(request)
        except AdapterError as exc:
            self._finish_adapter_error(ctx, row, exc)
        except Exception as exc:
            self._finish_unknown(ctx, row, exc)
        return self._finish_receipt(ctx, row, receipt)

    def _require_policy(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> None:
        try:
            require_manual_deployment_policy(ctx, row, self._config, self._infrastructure)
        except ManualDeploymentPolicyFailure as exc:
            raise ConflictDetected(
                f"source merge {row.delivery_id} blocked because manual deployment policy was not verified",
                details=exc.error_ref,
            ) from exc

    def _reconcile(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        target: PullRequestTarget,
    ) -> ReleaseDeliveryRecord:
        try:
            receipt = self._source.lookup_merge(target)
        except Exception as exc:
            raise ExternalReleaseDeliveryOutcomeUnknown("source merge lookup is not conclusive") from exc
        if receipt.status is SourceControlMergeStatus.AMBIGUOUS:
            raise ExternalReleaseDeliveryOutcomeUnknown("source merge remains ambiguous")
        if not receipt_matches_delivery(receipt, row):
            raise ExternalReleaseDeliveryOutcomeUnknown("source merge lookup identity does not match its intent")
        result = source_receipt_ref(receipt)
        status = "landed" if receipt.status is SourceControlMergeStatus.LANDED else "absent"
        settled = self._settle(ctx, row, status, result, receipt.merge_commit_sha)
        if settled.status != "landed":
            raise ConflictDetected("source merge was authoritatively absent after reconciliation")
        return settled

    def _finish_receipt(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        receipt: SourceControlMergeReceipt,
    ) -> ReleaseDeliveryRecord:
        if not receipt_matches_delivery(receipt, row):
            self._finish_unknown(ctx, row, ValueError("source merge receipt identity mismatch"))
        result = source_receipt_ref(receipt)
        if receipt.status is SourceControlMergeStatus.LANDED:
            return self._complete(ctx, row, "landed", result, None, receipt.merge_commit_sha)
        if receipt.status is SourceControlMergeStatus.ABSENT:
            completed = self._complete(ctx, row, "absent", result, None, None)
            raise ConflictDetected(f"source merge {completed.delivery_id} was not created")
        completed = self._complete(ctx, row, "ambiguous", result, {"kind": "outcome_unknown"}, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"source merge {completed.delivery_id} is ambiguous")

    def _finish_adapter_error(self, ctx: RequestContext, row: ReleaseDeliveryRecord, exc: AdapterError) -> NoReturn:
        error = adapter_failure_payload(exc)
        if exc.failure.kind in _KNOWN_NOT_COMMITTED_KINDS:
            completed = self._complete(ctx, row, "failed", None, error, None)
            raise ConflictDetected(f"source merge {completed.delivery_id} failed before acceptance") from exc
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"delivery {completed.delivery_id} outcome is unknown") from exc

    def _finish_unknown(self, ctx: RequestContext, row: ReleaseDeliveryRecord, exc: Exception) -> NoReturn:
        error = dict(self._runtime._error_payload(exc, ctx, run_id=row.ai_run_id))
        error["kind"] = "outcome_unknown"
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"delivery {completed.delivery_id} outcome is unknown") from exc

    def _complete(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: ReleaseDeliveryTerminalStatus,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord:
        completed = self._ledger.complete(ctx, row, status, result_ref, error_ref, provider_resource_id)
        if completed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("delivery completion lost its dispatch fence")
        return completed

    def _settle(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: str,
        result_ref: dict[str, object],
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord:
        completed = self._ledger.settle_lookup(ctx, row, status, result_ref, None, provider_resource_id)
        if completed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("delivery reconciliation lost its fence")
        return completed


def _target_matches_row(target: PullRequestTarget, row: ReleaseDeliveryRecord) -> bool:
    return _publication_target_identity(target) == _stored_target_identity(row)


def _publication_target_identity(target: PullRequestTarget) -> tuple[object, ...]:
    binding = target.candidate_binding
    binding_identity: tuple[object, ...] | None = None
    if binding is not None:
        binding_identity = (
            binding.expected_base_sha,
            binding.expected_tree_sha,
            binding.expected_head_ref,
            binding.manifest.artifact_path,
            binding.manifest.manifest_fingerprint,
        )
    return (
        target.repository.provider,
        target.repository.repository_id,
        target.repository.owner,
        target.repository.name,
        target.pull_number,
        target.expected_base_ref,
        target.expected_head_sha,
        binding_identity,
    )


def _stored_target_identity(row: ReleaseDeliveryRecord) -> tuple[object, ...]:
    candidate = row.candidate_ref or {}
    return (
        row.provider,
        row.target_ref.get("repositoryId"),
        row.target_ref.get("repositoryOwner"),
        row.target_ref.get("repositoryName"),
        row.target_ref.get("pullNumber"),
        row.target_ref.get("baseRef"),
        row.target_ref.get("headSha"),
        (
            candidate.get("baseSha"),
            candidate.get("candidateTreeSha"),
            candidate.get("headRef"),
            candidate.get("manifestArtifactPath"),
            candidate.get("manifestFingerprint"),
        ),
    )


__all__ = ["ExternalReleaseSourceCoordinator"]
