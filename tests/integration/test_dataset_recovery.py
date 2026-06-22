from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import insert, select


class _ClaimMissDatasetTransactionRepository:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.claim_calls = 0

    def list_recoverable_transactions(self, **kwargs: Any) -> Any:
        return self.delegate.list_recoverable_transactions(**kwargs)

    def claim_stale_open_transaction(self, **_kwargs: Any) -> None:
        self.claim_calls += 1
        return None


class _RecordingStorage:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.deleted_staging: list[tuple[str, str, str]] = []

    def delete_staging_transaction(self, *, tenant_id: str, dataset_id: str, transaction_id: str) -> bool:
        self.deleted_staging.append((tenant_id, dataset_id, transaction_id))
        return self.delegate.delete_staging_transaction(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            transaction_id=transaction_id,
        )


def _seed_transaction(
    foundry: FoundryLite,
    dataset_id: str,
    *,
    tx_id: str,
    created_at: str,
    status: str = "OPEN",
    metadata: dict[str, object] | None = None,
) -> None:
    ctx = demo_admin_context()
    with foundry.engine.begin() as conn:
        conn.execute(
            insert(db.dataset_transactions).values(
                id=tx_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                branch="main",
                tx_type="SNAPSHOT",
                status=status,
                base_version_id=None,
                committed_version_id=None,
                schema_version=None,
                created_by=ctx.actor_user_id,
                created_at=created_at,
                committed_at=None,
                metadata=metadata or {"attempt": "oom"},
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
    _seed_transaction(foundry, dataset_id, tx_id="dstx_stale", created_at="2026-06-10T00:00:00Z")
    # A freshly opened transaction must not be swept by the watchdog cutoff.
    _seed_transaction(foundry, dataset_id, tx_id="dstx_recent", created_at="2026-06-15T12:00:00Z")

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


def test_stale_recovery_skips_cleanup_when_watchdog_claim_loses(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.events", ctx=ctx, primary_key=["id"])
    dataset_id = str(foundry.datasets.get("raw.events", ctx=ctx)["id"])
    _seed_transaction(foundry, dataset_id, tx_id="dstx_race", created_at="2026-06-10T00:00:00Z")
    repository = _ClaimMissDatasetTransactionRepository(dependencies.dataset_transaction_repository)
    storage = _RecordingStorage(dependencies.dataset_storage)
    racing_foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            dataset_transaction_repository=repository,
            dataset_storage=storage,
        )
    )

    aborted = racing_foundry.datasets.abort_stale_open_transactions("2026-06-12T00:00:00Z", ctx=ctx)

    assert aborted == []
    assert repository.claim_calls == 1
    assert storage.deleted_staging == []


def test_stale_recovery_finishes_previously_claimed_watchdog_abort(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.events", ctx=ctx, primary_key=["id"])
    dataset_id = str(foundry.datasets.get("raw.events", ctx=ctx)["id"])
    _seed_transaction(
        foundry,
        dataset_id,
        tx_id="dstx_abort_claimed",
        created_at="2026-06-10T00:00:00Z",
        status="ABORTING",
        metadata={
            "attempt": "oom",
            "abortedBy": "watchdog",
            "abortReason": "stale_open_transaction",
            "abortClaimedAt": "2026-06-12T00:00:00Z",
        },
    )

    aborted = foundry.datasets.abort_stale_open_transactions("2026-06-12T00:00:00Z", ctx=ctx)

    with foundry.engine.begin() as conn:
        row = dict(
            conn.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == "dstx_abort_claimed"))
            .mappings()
            .one()
        )
    assert aborted == ["dstx_abort_claimed"]
    assert row["status"] == "ABORTED"
