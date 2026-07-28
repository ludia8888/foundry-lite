from typing import Any, cast

from foundry_lite.application.ports.media_repository import MediaTransactionRecord
from foundry_lite.application.services.pipeline_media_set_output_reconciliation import (
    handle_media_output_commit_failure,
)
from foundry_lite.domain.context import RequestContext


def test_aborted_media_output_failure_does_not_abort_or_reconcile_again() -> None:
    transaction = MediaTransactionRecord(
        media_transaction_id="mtx-aborted",
        tenant_id="tenant-demo",
        media_set_id="media-set-1",
        status="ABORTED",
        mode="SNAPSHOT",
        idempotency_key="pipeline-output:run-1:output",
        request_fingerprint="request-fingerprint",
        opened_at="2026-07-28T00:00:00Z",
        aborted_at="2026-07-28T00:01:00Z",
    )

    handle_media_output_commit_failure(
        node=cast(Any, object()),
        source=cast(Any, object()),
        inputs={},
        target_ref="media.output",
        target=cast(Any, object()),
        transaction_attempt=cast(Any, object()),
        transaction=transaction,
        entries=(),
        committed_versions=(),
        media_transactions=cast(Any, object()),
        ctx=RequestContext(tenant_id="tenant-demo"),
        exc=RuntimeError("original failure"),
    )
