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
    require_open_media_transaction,
    validate_output_versions,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2CommittedOutputReconciliationRequired,
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
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
        transaction = self._transaction_loader(transaction_attempt.media_transaction_id)
        try:
            if transaction.status == "COMMITTED":
                return self._replay_committed(
                    node, source, inputs, target_ref, target, transaction_attempt, request_fingerprint, entries
                )
            require_open_media_transaction(transaction)
            return self._commit_open(
                node, source, inputs, target_ref, target, transaction_attempt, request_fingerprint, entries
            )
        except PipelineV2CommittedOutputReconciliationRequired:
            raise
        except Exception as exc:
            self._abort_open_preserving_error(transaction, exc)
            raise

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
        self._stage_missing_entries(node, source, target, transaction_attempt, request_fingerprint, entries)
        versions = self._versions_loader(transaction_attempt.media_transaction_id)
        self._validate(versions, entries, target, request_fingerprint, transaction_attempt.generation)
        fallback = transaction_reconciliation_artifact(
            node, source, inputs, target_ref, target, transaction_attempt, entries
        )
        result = self._media_transactions.commit(
            self._ctx,
            media_transaction_id=transaction_attempt.media_transaction_id,
            before_commit=self._require_active_lease,
        )
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
        transaction: MediaTransactionRecord,
        exc: Exception,
    ) -> None:
        if transaction.status != "OPEN":
            return
        try:
            self._media_transactions.abort(
                self._ctx,
                media_transaction_id=transaction.media_transaction_id,
                error={"code": getattr(exc, "code", type(exc).__name__)},
            )
        except Exception as abort_exc:
            exc.add_note(f"media output abort failed: {type(abort_exc).__name__}")
