"""Durable dispatch and reconciliation for an exact source candidate PR."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NoReturn

from foundry_lite.application.dependency_release import (
    GovernedReleaseDeliveryConfig,
    PullRequestSnapshot,
    PullRequestTarget,
    ReleaseDeliveryRecord,
    ReleaseDeliveryTerminalStatus,
    SourceControlReleasePort,
    SourceRepositoryRef,
)
from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateManifest,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
)
from foundry_lite.application.services.aip.external_release_delivery_ledger import ExternalReleaseDeliveryLedger
from foundry_lite.application.services.aip.external_release_delivery_lineage import root_delivery_lineage
from foundry_lite.application.services.aip.external_release_delivery_payloads import delivery_projection
from foundry_lite.application.services.aip.external_release_delivery_support import (
    ExternalReleaseDeliveryOutcomeUnknown,
    mutation_identity,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    prepare_command as _prepare_command,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    proposal_id as _proposal_id,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    required_text as _required_text,
)
from foundry_lite.application.services.aip.external_release_source_publication_payloads import (
    publication_receipt_matches,
    require_publication_candidate_binding,
    require_publication_replay_binding,
    source_publication_candidate_ref,
    source_publication_manifest,
    source_publication_receipt_ref,
    source_publication_request_for_proposal,
    source_publication_request_from_record,
    source_publication_target_from_record,
    source_publication_target_ref,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class ExternalReleaseSourcePublicationWorkflow:
    """Build, persist, publish, and re-read one exact proposal manifest."""

    def __init__(
        self,
        source_adapter: SourceControlReleasePort,
        config: GovernedReleaseDeliveryConfig,
        ledger: ExternalReleaseDeliveryLedger,
        runtime_service: RuntimeEvidenceBoundary,
    ) -> None:
        self._source = source_adapter
        self._config = config
        self._ledger = ledger
        self._coordinator = ExternalReleaseSourcePublicationCoordinator(source_adapter, ledger, runtime_service)

    def publish(
        self,
        ctx: RequestContext,
        release_kind: str,
        proposal: dict[str, object] | Mapping[str, object],
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        idempotency_key = _required_text(arguments, "idempotencyKey")
        replay = self._find_replay(ctx, idempotency_key)
        base_sha = _required_text(replay.target_ref, "baseSha") if replay is not None else self._fresh_base_sha()
        governed, manifest = source_publication_manifest(
            ctx,
            release_kind,
            proposal,
            self._config,
            consumer_osdk_application_id=_optional_text(arguments, "consumerOsdkApplicationId"),
            consumer_osdk_compliance=_optional_mapping(arguments, "consumerOsdkCompliance"),
            expected_source_commit=base_sha,
        )
        request = self._request(proposal, release_kind, manifest, idempotency_key, base_sha)
        if replay is not None:
            require_publication_replay_binding(replay, ctx, request)
            row = replay
        else:
            row = self._prepare(ctx, request, source_publication_candidate_ref(request, governed))
        return self._coordinator.resume(ctx, row, request)

    def fresh_snapshot(
        self,
        ctx: RequestContext,
        proposal: Mapping[str, object],
    ) -> PullRequestSnapshot:
        return self.fresh_snapshot_with_publication(ctx, proposal)[1]

    def fresh_snapshot_with_publication(
        self,
        ctx: RequestContext,
        proposal: Mapping[str, object],
        publication_id: str | None = None,
    ) -> tuple[ReleaseDeliveryRecord, PullRequestSnapshot]:
        """Inspect immutable stored publication bytes and return its exact parent row."""

        proposal_id = _proposal_id(proposal)
        publication = (
            self._ledger.get(ctx, publication_id)
            if publication_id is not None
            else self._ledger.latest(ctx, proposal_id, "source_publish")
        )
        if publication is None or publication.status != "landed":
            raise ConflictDetected("source candidate must be durably published before merge review")
        if publication.proposal_id != proposal_id:
            raise ConflictDetected("source publication parent does not match the governed proposal")
        request = source_publication_request_from_record(publication, self._config)
        return publication, self._inspect_published(publication, request)

    def _inspect_published(
        self,
        publication: ReleaseDeliveryRecord,
        request: SourceCandidatePublicationRequest,
    ) -> PullRequestSnapshot:
        require_publication_candidate_binding(publication, request)
        target = source_publication_target_from_record(publication, request)
        snapshot = self._source.inspect_pull_request(target)
        _require_publication_snapshot(snapshot, request, target)
        return snapshot

    def _find_replay(self, ctx: RequestContext, idempotency_key: str) -> ReleaseDeliveryRecord | None:
        repository = _repository(self._config)
        return self._ledger.find_by_idempotency(
            ctx,
            repository.provider,
            "source_publish",
            idempotency_key,
        )

    def _request(
        self,
        proposal: Mapping[str, object],
        release_kind: str,
        manifest: SourceCandidateManifest,
        idempotency_key: str,
        base_sha: str,
    ) -> SourceCandidatePublicationRequest:
        return source_publication_request_for_proposal(
            release_kind,
            proposal,
            self._config,
            manifest,
            base_sha,
            idempotency_key,
        )

    def _fresh_base_sha(self) -> str:
        repository = _repository(self._config)
        snapshot = self._source.inspect_source_ref(repository, self._config.source_base_ref)
        if snapshot.repository != repository or snapshot.ref != self._config.source_base_ref:
            raise ConflictDetected("source-control adapter returned a base ref outside the server binding")
        return snapshot.commit_sha

    def _prepare(
        self,
        ctx: RequestContext,
        request: SourceCandidatePublicationRequest,
        candidate: dict[str, object],
    ) -> ReleaseDeliveryRecord:
        ai_run_id, _ = mutation_identity(ctx)
        lineage = root_delivery_lineage(ctx, request.release_kind, ai_run_id)
        command = _prepare_command(
            ctx,
            request.proposal_id,
            request.repository.provider,
            "source_publish",
            source_publication_target_ref(request),
            candidate,
            request.expected_base_ref,
            request.idempotency_key,
            None,
            lineage,
        )
        return self._ledger.prepare(ctx, command)


class ExternalReleaseSourcePublicationCoordinator:
    """Advance a write-ahead candidate publication without blind replay."""

    def __init__(
        self,
        source_adapter: SourceControlReleasePort,
        ledger: ExternalReleaseDeliveryLedger,
        runtime_service: RuntimeEvidenceBoundary,
    ) -> None:
        self._source = source_adapter
        self._ledger = ledger
        self._runtime = runtime_service

    def resume(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        request: SourceCandidatePublicationRequest,
    ) -> dict[str, object]:
        if row.status == "landed":
            return delivery_projection(row)
        if row.status in {"dispatching", "ambiguous"}:
            return delivery_projection(self._reconcile(ctx, row, request))
        if row.status in {"failed", "absent"}:
            raise ConflictDetected("source candidate publication previously reached a known non-success outcome")
        claimed = self._ledger.claim(ctx, row)
        if claimed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication dispatch fence was lost")
        return delivery_projection(self._dispatch(ctx, claimed, request))

    def _dispatch(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        request: SourceCandidatePublicationRequest,
    ) -> ReleaseDeliveryRecord:
        try:
            receipt = self._source.publish_pull_request_candidate(request)
        except AdapterError as exc:
            self._finish_adapter_error(ctx, row, exc)
        except Exception as exc:
            self._finish_unknown(ctx, row, exc)
        return self._finish_receipt(ctx, row, receipt)

    def _reconcile(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        request: SourceCandidatePublicationRequest,
    ) -> ReleaseDeliveryRecord:
        try:
            receipt = self._source.lookup_pull_request_candidate(request)
        except Exception as exc:
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication lookup is not conclusive") from exc
        self._require_receipt(row, receipt)
        if receipt.status is SourceCandidatePublicationStatus.PUBLISHED:
            return self._settle_published(ctx, row, receipt)
        if receipt.status is SourceCandidatePublicationStatus.AMBIGUOUS:
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication remains ambiguous")
        return self._recover_known_incomplete(ctx, row, request)

    def _recover_known_incomplete(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        request: SourceCandidatePublicationRequest,
    ) -> ReleaseDeliveryRecord:
        try:
            receipt = self._source.publish_pull_request_candidate(request)
        except Exception as exc:
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication recovery is not conclusive") from exc
        self._require_receipt(row, receipt)
        if receipt.status is SourceCandidatePublicationStatus.PUBLISHED:
            return self._settle_published(ctx, row, receipt)
        if row.status == "dispatching":
            return self._finish_receipt(ctx, row, receipt)
        raise ExternalReleaseDeliveryOutcomeUnknown("source publication recovery remains incomplete")

    def _finish_receipt(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        receipt: SourceCandidatePublicationReceipt,
    ) -> ReleaseDeliveryRecord:
        self._require_receipt(row, receipt)
        result = source_publication_receipt_ref(receipt)
        if receipt.status is SourceCandidatePublicationStatus.PUBLISHED:
            return self._complete(ctx, row, "landed", result, None, _resource_id(receipt))
        if receipt.status is SourceCandidatePublicationStatus.ABSENT:
            completed = self._complete(ctx, row, "absent", result, None, None)
            raise ConflictDetected(f"source candidate {completed.delivery_id} was not published")
        completed = self._complete(ctx, row, "ambiguous", result, {"kind": "outcome_unknown"}, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"source candidate {completed.delivery_id} is incomplete")

    def _settle_published(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        receipt: SourceCandidatePublicationReceipt,
    ) -> ReleaseDeliveryRecord:
        result = source_publication_receipt_ref(receipt)
        if row.status == "dispatching":
            return self._complete(ctx, row, "landed", result, None, _resource_id(receipt))
        settled = self._ledger.settle_lookup(ctx, row, "landed", result, None, _resource_id(receipt))
        if settled is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication reconciliation lost its fence")
        return settled

    def _require_receipt(
        self,
        row: ReleaseDeliveryRecord,
        receipt: SourceCandidatePublicationReceipt,
    ) -> None:
        if not publication_receipt_matches(receipt, row):
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication receipt does not match its intent")

    def _finish_adapter_error(self, ctx: RequestContext, row: ReleaseDeliveryRecord, exc: AdapterError) -> NoReturn:
        error = adapter_failure_payload(exc)
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        message = f"source candidate {completed.delivery_id} outcome is unknown"
        raise ExternalReleaseDeliveryOutcomeUnknown(message) from exc

    def _finish_unknown(self, ctx: RequestContext, row: ReleaseDeliveryRecord, exc: Exception) -> NoReturn:
        error = dict(self._runtime._error_payload(exc, ctx, run_id=row.ai_run_id))
        error["kind"] = "outcome_unknown"
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        message = f"source candidate {completed.delivery_id} outcome is unknown"
        raise ExternalReleaseDeliveryOutcomeUnknown(message) from exc

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
            raise ExternalReleaseDeliveryOutcomeUnknown("source publication completion lost its dispatch fence")
        return completed


def _resource_id(receipt: SourceCandidatePublicationReceipt) -> str | None:
    return f"pull:{receipt.pull_number}" if receipt.pull_number is not None else None


def _repository(config: GovernedReleaseDeliveryConfig) -> SourceRepositoryRef:
    repository = config.source_repository
    if repository is None:
        raise ConflictDetected("source-control repository is not configured")
    return repository


def _optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConflictDetected(f"{key} must be a non-empty string")
    return value.strip()


def _optional_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConflictDetected(f"{key} must be an object")
    return value


def _require_publication_snapshot(
    snapshot: PullRequestSnapshot,
    request: SourceCandidatePublicationRequest,
    target: PullRequestTarget,
) -> None:
    binding = snapshot.target.candidate_binding
    is_exact = (
        snapshot.target == target
        and snapshot.target.repository == request.repository
        and snapshot.target.expected_base_ref == request.expected_base_ref
        and snapshot.head_ref == request.expected_head_ref
        and snapshot.base_sha == request.expected_base_sha
        and binding is not None
        and binding.expected_base_sha == request.expected_base_sha
        and binding.expected_head_ref == request.expected_head_ref
        and binding.manifest.manifest_fingerprint == request.manifest.manifest_fingerprint
    )
    if not is_exact:
        raise ConflictDetected("source pull request no longer matches the published governed candidate")


__all__ = ["ExternalReleaseSourcePublicationCoordinator", "ExternalReleaseSourcePublicationWorkflow"]
