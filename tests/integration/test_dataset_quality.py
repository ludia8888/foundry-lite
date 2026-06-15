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


def test_dataset_health_check_reads_candidate_not_latest(
    core: FoundryLiteCore,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    core.ensure_dataset("raw.health_orders", ctx=ctx, primary_key=["order_id"])
    latest_csv = tmp_path / "latest_orders.csv"
    latest_csv.write_text("order_id,amount\nO-1,100\n", encoding="utf-8")
    committed = core.upload_csv("raw.health_orders", latest_csv, ctx=ctx)

    invalid_candidate = tmp_path / "candidate_orders.csv"
    invalid_candidate.write_text("order_id,amount\nO-2,200\nO-2,201\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        core.upload_csv("raw.health_orders", invalid_candidate, ctx=ctx)

    versions = core.list_dataset_versions("raw.health_orders", ctx=ctx)
    preview = core.preview_dataset("raw.health_orders", ctx=ctx)
    failures = exc_info.value.details["failures"]
    assert [version["id"] for version in versions] == [committed.version_id]
    assert [(row["order_id"], row["amount"]) for row in preview] == [("O-1", 100)]
    assert any(failure["check"] == "unique" and failure["status"] == "failed" for failure in failures)
