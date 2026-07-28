"""Failure reconciliation around the Media Set output domain commit point."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.services.pipeline_media_output_port_types import (
    MediaItemVersionRecord,
    MediaSetRecord,
    MediaTransactionRecord,
)
from foundry_lite.application.services.pipeline_media_output_service_types import (
    MediaTransactionService,
)
from foundry_lite.application.services.pipeline_media_set_output_artifact import (
    output_artifact,
)
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    MediaOutputEntry,
    MediaOutputTransactionAttempt,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2CommittedOutputReconciliationRequired,
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
)
from foundry_lite.domain.context import RequestContext


def handle_media_output_commit_failure(
    *,
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    target_ref: str,
    target: MediaSetRecord,
    transaction_attempt: MediaOutputTransactionAttempt,
    transaction: MediaTransactionRecord,
    entries: Sequence[MediaOutputEntry],
    committed_versions: Sequence[MediaItemVersionRecord],
    media_transactions: MediaTransactionService,
    ctx: RequestContext,
    exc: Exception,
) -> None:
    if transaction.status == "OPEN":
        media_transactions.abort(
            ctx,
            media_transaction_id=transaction.media_transaction_id,
            error={"code": getattr(exc, "code", type(exc).__name__)},
        )
        return
    if transaction.status != "COMMITTED":
        return
    artifact = output_artifact(
        node,
        source,
        inputs,
        target_ref,
        target,
        transaction_attempt,
        entries,
        committed_versions,
    )
    raise PipelineV2CommittedOutputReconciliationRequired(
        "pipeline Media Set output committed but post-commit validation failed",
        artifact=artifact,
    ) from exc
