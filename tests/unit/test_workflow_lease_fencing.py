"""Workflow run lease release must be fenced at the CAS, not only in Python.

release_workflow_run_lease re-reads the row and checks the lease owner/token in
Python before the status CAS. Under real concurrency the row can be taken over
between that read and the CAS, so the Python guard has a TOCTOU window: a stale
worker whose read still shows its own lease will pass the guard and then a
status-only CAS overwrites the live owner's row (and its cursor). The fix puts
the lease owner/token into the UPDATE WHERE so the CAS itself rejects a
superseded worker. This test simulates the interleaving by making the internal
re-read return the stale row while the database already holds the new lease.
"""

from __future__ import annotations

from typing import Any, cast

import foundry_lite.infrastructure.repositories.runtime_workflow_runs as runtime_workflow_runs
import pytest
from foundry_lite.application.ports.workflow_adapter import WorkflowRunRecord
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.runtime_repository import SqlAlchemyRuntimeRepository
from sqlalchemy import create_engine


def _record(
    *,
    run_id: str,
    owner: str,
    token: str,
    expires_at: str,
    started_at: str,
) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        workflow_run_id=run_id,
        tenant_id="tenant-demo",
        workflow_name="ContinuousStreamArchiveWorker",
        workflow_profile="local",
        status="running",
        idempotency_key="stream-archive:raw.orders:orders:group:0",
        request_fingerprint="fingerprint-lease",
        input={"datasetRef": "raw.orders"},
        output={
            "workerLease": {
                "ownerId": owner,
                "leaseToken": token,
                "leaseExpiresAt": expires_at,
                "lastHeartbeatAt": "2026-06-10T00:00:00Z",
            },
            "cursor": {"offset": 42},
        },
        error=None,
        dataset_id="ds_orders",
        audit_event_id=None,
        attempts=1,
        created_at="2026-06-10T00:00:00Z",
        started_at=started_at,
        completed_at=None,
    )


def test_stale_release_cannot_overwrite_a_taken_over_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    db.create_database(engine)
    repository = SqlAlchemyRuntimeRepository(engine)
    run_id = "flite:workflow:run:1"

    with engine.begin() as conn:
        worker1 = repository.claim_workflow_run_lease(
            transaction=conn,
            record=_record(
                run_id=run_id,
                owner="worker-1",
                token="lease-1",
                expires_at="2026-06-10T00:01:00Z",
                started_at="2026-06-10T00:00:00Z",
            ),
            lease_owner_id="worker-1",
            now="2026-06-10T00:00:00Z",
        )
        assert worker1 is not None
        stale_row = dict(worker1)

        # worker-2 takes over the expired lease; the DB now holds lease-2 with a
        # different cursor.
        worker2 = repository.claim_workflow_run_lease(
            transaction=conn,
            record=_record(
                run_id=run_id,
                owner="worker-2",
                token="lease-2",
                expires_at="2026-06-10T00:02:00Z",
                started_at="2026-06-10T00:01:01Z",
            ),
            lease_owner_id="worker-2",
            now="2026-06-10T00:01:01Z",
        )
        assert worker2 is not None
        worker2_cursor = cast(dict[str, Any], worker2["output"])["cursor"]

        # Simulate the TOCTOU window: worker-1's release re-reads the row, but its
        # read still reflects the pre-takeover (lease-1) state. The DB row is
        # already lease-2. The real re-read after the CAS must still work, so only
        # the first internal read returns the stale row.
        real_workflow_run_by_id = runtime_workflow_runs.workflow_run_by_id
        calls = {"n": 0}

        def _stale_first_read(**kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                return dict(stale_row)
            return real_workflow_run_by_id(**kwargs)

        monkeypatch.setattr(runtime_workflow_runs, "workflow_run_by_id", _stale_first_read)

        stale_release = repository.release_workflow_run_lease(
            transaction=conn,
            tenant_id="tenant-demo",
            workflow_run_id=run_id,
            lease_owner_id="worker-1",
            lease_token="lease-1",
            status="succeeded",
            output={"stopReason": "stale", "cursor": {"offset": 1}},
            error=None,
            completed_at="2026-06-10T00:01:02Z",
        )

    # The stale worker must not release a lease it no longer owns, and the live
    # owner's row (status + cursor) must be untouched.
    assert stale_release is None
    with engine.begin() as conn:
        row = repository.workflow_run_by_id(transaction=conn, tenant_id="tenant-demo", workflow_run_id=run_id)
    assert row is not None
    assert row["status"] == "running"
    output = cast(dict[str, Any], row["output"])
    assert output["workerLease"]["ownerId"] == "worker-2"
    assert output["workerLease"]["leaseToken"] == "lease-2"
    assert output["cursor"] == worker2_cursor
