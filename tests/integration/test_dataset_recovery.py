from __future__ import annotations

from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import insert, select


def _seed_open_transaction(foundry: FoundryLite, dataset_id: str, *, tx_id: str, created_at: str) -> None:
    ctx = demo_admin_context()
    with foundry.engine.begin() as conn:
        conn.execute(
            insert(db.dataset_transactions).values(
                id=tx_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                branch="main",
                tx_type="SNAPSHOT",
                status="OPEN",
                base_version_id=None,
                committed_version_id=None,
                schema_version=None,
                created_by=ctx.actor_user_id,
                created_at=created_at,
                committed_at=None,
                metadata={"attempt": "oom"},
            )
        )


def test_failed_upload_oom_leaves_recoverable_aborted_or_stale_open_tx(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.events", ctx=ctx, primary_key=["id"])
    dataset = foundry.datasets.get("raw.events", ctx=ctx)
    dataset_id = str(dataset["id"])

    # A process killed (OOM) between opening a dataset transaction and committing
    # leaves an OPEN row that never reaches a terminal state.
    _seed_open_transaction(foundry, dataset_id, tx_id="dstx_stale", created_at="2026-06-10T00:00:00Z")
    # A freshly opened transaction must not be swept by the watchdog cutoff.
    _seed_open_transaction(foundry, dataset_id, tx_id="dstx_recent", created_at="2026-06-15T12:00:00Z")

    aborted = foundry.datasets.abort_stale_open_transactions("2026-06-12T00:00:00Z", ctx=ctx)

    with foundry.engine.begin() as conn:
        rows = {row["id"]: dict(row) for row in conn.execute(select(db.dataset_transactions)).mappings()}
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]

    # The stale OPEN transaction is recovered to ABORTED with watchdog evidence;
    # the recent OPEN transaction is left alone, so an OOM-abandoned write becomes
    # recoverable instead of permanently blocking the dataset.
    assert aborted == ["dstx_stale"]
    assert rows["dstx_stale"]["status"] == "ABORTED"
    assert rows["dstx_stale"]["metadata"]["abortedBy"] == "watchdog"
    assert rows["dstx_stale"]["metadata"]["abortReason"] == "stale_open_transaction"
    assert rows["dstx_recent"]["status"] == "OPEN"
    assert any(event["event_type"] == "dataset.transaction.aborted_by_watchdog" for event in audit_events)
