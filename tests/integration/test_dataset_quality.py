from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed


def test_commit_dataset_version_aborts_when_primary_key_check_fails(
    core: FoundryLiteCore,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    core.ensure_dataset("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "duplicate_orders.csv"
    csv_path.write_text(
        "order_id,customer_id,source_status\nO-1,C-1,PENDING\nO-1,C-2,REVIEW\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed):
        core.upload_csv("raw.erp_orders", csv_path, ctx=ctx)

    runs = core.list_runs(ctx=ctx)
    assert runs["syncRuns"][0]["status"] == "FAILED"
    assert core.list_dataset_versions("raw.erp_orders", ctx=ctx) == []
