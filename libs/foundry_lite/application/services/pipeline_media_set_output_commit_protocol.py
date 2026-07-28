"""Media Set output transaction protocol with an explicit durable commit point."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from foundry_lite.application.services.media.transactions import MediaCommitResult
from foundry_lite.application.services.pipeline_media_output_port_types import (
    MediaItemVersionRecord,
    MediaSetRecord,
    MediaStorageAdapter,
    MediaTransactionRecord,
)
from foundry_lite.application.services.pipeline_media_output_service_types import (
    MediaTransactionService,
)
from foundry_lite.application.services.pipeline_media_set_output_artifact import (
    output_artifact,
    transaction_reconciliation_artifact,
)
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    MediaOutputEntry,
    MediaOutputTransactionAttempt,
)
from foundry_lite.application.services.pipeline_media_set_output_reconciliation import (
    committed_media_versions,
    raise_media_output_reconciliation,
    require_media_commit_receipt,
)
from foundry_lite.application.services.pipeline_media_set_output_support import (
    validate_output_versions,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2OutputCommitOutcomeUnknown,
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
)
from foundry_lite.application.services.runtime_error_payloads import (
    record_runtime_cleanup_failure,
)
from foundry_lite.domain.context import RequestContext

TransactionLoader = Callable[[str], MediaTransactionRecord]
VersionsLoader = Callable[[str], list[MediaItemVersionRecord]]
StageMissingEntries = Callable[
    [
        PipelineV2RuntimeNode,
        PipelineV2RuntimeArtifact,
        MediaSetRecord,
        MediaOutputTransactionAttempt,
        str,
        Sequence[MediaOutputEntry],
    ],
    None,
]


class PipelineMediaSetOutputCommitProtocol:
    def __init__(
        self,
        *,
        media_storage: MediaStorageAdapter,
        media_transactions: MediaTransactionService,
        ctx: RequestContext,
        require_active_lease: Callable[[object | None], None],
        transaction_loader: TransactionLoader,
        versions_loader: VersionsLoader,
        stage_missing_entries: StageMissingEntries,
    ) -> None:
        self._media_storage = media_storage
        self._media_transactions = media_transactions
        self._ctx = ctx
        self._require_active_lease = require_active_lease
        self._transaction_loader = transaction_loader
        self._versions_loader = versions_loader
        self._stage_missing_entries = stage_missing_entries

    def commit(
        self,
        *,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        inputs: RuntimeInputs,
        target_ref: str,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entries: Sequence[MediaOutputEntry],
    ) -> PipelineV2RuntimeArtifact:
        if transaction_attempt.transaction_status == "COMMITTED":
            return self._replay_committed(
                node, source, inputs, target_ref, target, transaction_attempt, request_fingerprint, entries
            )
        return self._commit_open(
            node, source, inputs, target_ref, target, transaction_attempt, request_fingerprint, entries
        )

    def _commit_open(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        inputs: RuntimeInputs,
        target_ref: str,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entries: Sequence[MediaOutputEntry],
    ) -> PipelineV2RuntimeArtifact:
        try:
            versions = self._prepare_open(node, source, target, transaction_attempt, request_fingerprint, entries)
        except Exception as exc:
            self._abort_open_preserving_error(transaction_attempt.media_transaction_id, exc)
            raise
        fallback = transaction_reconciliation_artifact(
            node, source, inputs, target_ref, target, transaction_attempt, entries
        )
        unknown = self._unknown_reconciliation_artifact(
            node, source, inputs, target_ref, target, transaction_attempt, entries
        )
        result = self._commit_with_reconciliation(transaction_attempt, fallback, unknown)
        return self._artifact_after_commit(
            node,
            source,
            inputs,
            target_ref,
            target,
            transaction_attempt,
            request_fingerprint,
            entries,
            versions,
            result,
            fallback,
        )

    @staticmethod
    def _unknown_reconciliation_artifact(
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        inputs: RuntimeInputs,
        target_ref: str,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        entries: Sequence[MediaOutputEntry],
    ) -> PipelineV2RuntimeArtifact:
        return transaction_reconciliation_artifact(
            node,
            source,
            inputs,
            target_ref,
            target,
            transaction_attempt,
            entries,
            commit_outcome="UNKNOWN",
        )

    def _prepare_open(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entries: Sequence[MediaOutputEntry],
    ) -> list[MediaItemVersionRecord]:
        self._stage_missing_entries(node, source, target, transaction_attempt, request_fingerprint, entries)
        versions = self._versions_loader(transaction_attempt.media_transaction_id)
        self._validate(versions, entries, target, request_fingerprint, transaction_attempt.generation)
        return versions

    def _commit_with_reconciliation(
        self,
        transaction_attempt: MediaOutputTransactionAttempt,
        committed_fallback: PipelineV2RuntimeArtifact,
        unknown_fallback: PipelineV2RuntimeArtifact,
    ) -> MediaCommitResult:
        try:
            return self._media_transactions.commit(
                self._ctx,
                media_transaction_id=transaction_attempt.media_transaction_id,
                before_commit=self._require_active_lease,
            )
        except Exception as exc:
            self._handle_commit_failure(
                transaction_attempt.media_transaction_id,
                committed_fallback,
                unknown_fallback,
                exc,
            )
            raise

    def _handle_commit_failure(
        self,
        media_transaction_id: str,
        committed_fallback: PipelineV2RuntimeArtifact,
        unknown_fallback: PipelineV2RuntimeArtifact,
        exc: Exception,
    ) -> None:
        status = self._transaction_status_after_commit_error(media_transaction_id, exc)
        if status == "COMMITTED":
            raise_media_output_reconciliation(committed_fallback, exc)
        if status == "OPEN":
            self._abort_open_preserving_error(media_transaction_id, exc)
            return
        if status == "ABORTED":
            return
        raise PipelineV2OutputCommitOutcomeUnknown(
            "pipeline Media Set output commit outcome is unknown",
            artifact=unknown_fallback,
        ) from exc

    def _transaction_status_after_commit_error(
        self,
        media_transaction_id: str,
        primary: Exception,
    ) -> str | None:
        try:
            return self._transaction_loader(media_transaction_id).status
        except Exception as read_error:
            record_runtime_cleanup_failure(
                primary,
                operation="mediaTransactionCommitStateRead",
                cleanup_error=read_error,
            )
            return None

    def _artifact_after_commit(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        inputs: RuntimeInputs,
        target_ref: str,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entries: Sequence[MediaOutputEntry],
        versions: Sequence[MediaItemVersionRecord],
        result: MediaCommitResult,
        fallback: PipelineV2RuntimeArtifact,
    ) -> PipelineV2RuntimeArtifact:
        receipt = fallback
        try:
            require_media_commit_receipt(result, versions)
            committed_versions = committed_media_versions(versions, result.committed_at)
            receipt = output_artifact(
                node, source, inputs, target_ref, target, transaction_attempt, entries, committed_versions
            )
            self._validate(
                committed_versions,
                entries,
                target,
                request_fingerprint,
                transaction_attempt.generation,
                is_committed=True,
            )
        except Exception as exc:
            raise_media_output_reconciliation(receipt, exc)
        return receipt

    def _replay_committed(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
        inputs: RuntimeInputs,
        target_ref: str,
        target: MediaSetRecord,
        transaction_attempt: MediaOutputTransactionAttempt,
        request_fingerprint: str,
        entries: Sequence[MediaOutputEntry],
    ) -> PipelineV2RuntimeArtifact:
        fallback = transaction_reconciliation_artifact(
            node, source, inputs, target_ref, target, transaction_attempt, entries
        )
        try:
            versions = self._versions_loader(transaction_attempt.media_transaction_id)
            artifact = output_artifact(node, source, inputs, target_ref, target, transaction_attempt, entries, versions)
            self._validate(
                versions,
                entries,
                target,
                request_fingerprint,
                transaction_attempt.generation,
                is_committed=True,
            )
        except Exception as exc:
            raise_media_output_reconciliation(fallback, exc)
        return artifact

    def _validate(
        self,
        versions: Sequence[MediaItemVersionRecord],
        entries: Sequence[MediaOutputEntry],
        target: MediaSetRecord,
        request_fingerprint: str,
        generation: int,
        *,
        is_committed: bool = False,
    ) -> None:
        validate_output_versions(
            versions,
            entries,
            target,
            request_fingerprint,
            generation,
            self._media_storage,
            is_committed=is_committed,
        )

    def _abort_open_preserving_error(
        self,
        media_transaction_id: str,
        exc: Exception,
    ) -> None:
        try:
            self._media_transactions.abort(
                self._ctx,
                media_transaction_id=media_transaction_id,
                error={"code": getattr(exc, "code", type(exc).__name__)},
            )
        except Exception as abort_exc:
            record_runtime_cleanup_failure(
                exc,
                operation="mediaTransactionAbort",
                cleanup_error=abort_exc,
            )
            exc.add_note(f"media output abort failed: {type(abort_exc).__name__}")
