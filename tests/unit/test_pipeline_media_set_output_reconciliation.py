from types import SimpleNamespace
from typing import Any, cast

import pytest
from foundry_lite.application.services.media.transactions import MediaCommitResult
from foundry_lite.application.services.pipeline_media_output_port_types import (
    MediaItemVersionRecord,
)
from foundry_lite.application.services.pipeline_media_set_output_reconciliation import (
    require_media_commit_receipt,
)
from foundry_lite.domain.errors import ConflictDetected


def test_media_commit_receipt_must_match_prevalidated_version_coordinates() -> None:
    versions = [
        cast(
            MediaItemVersionRecord,
            cast(Any, SimpleNamespace(media_item_version_id="mver-1")),
        )
    ]
    matching = MediaCommitResult(
        media_transaction_id="mtx-1",
        committed_version_ids=("mver-1",),
        head_version_id_by_item={},
        committed_at="2026-07-28T00:00:00Z",
    )
    mismatched = MediaCommitResult(
        media_transaction_id="mtx-1",
        committed_version_ids=("mver-other",),
        head_version_id_by_item={},
        committed_at="2026-07-28T00:00:00Z",
    )

    require_media_commit_receipt(matching, versions)
    with pytest.raises(ConflictDetected, match="does not match"):
        require_media_commit_receipt(mismatched, versions)
