from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from foundry_lite.application.ports import (
    OsdkApplicationRepository,
    OsdkMcpSessionRecord,
    OsdkMcpSessionRow,
    TransactionContext,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyOsdkApplicationRepository
from sqlalchemy import create_engine


def test_mcp_stream_lease_cas_reclaims_expiry_and_rejects_stale_release() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository: OsdkApplicationRepository = SqlAlchemyOsdkApplicationRepository(engine)
    with engine.begin() as conn:
        repository.insert_mcp_session_or_get_existing(transaction=conn, record=_mcp_session())
        first = _claim_stream(repository, conn, "lease-a", "00:09:00", "00:10:00")
        blocked = _claim_stream(repository, conn, "lease-b", "00:09:30", "00:10:30")
        stale_release = _release(repository, conn, "lease-b", "00:09:40")
        reclaimed = _claim_stream(repository, conn, "lease-b", "00:10:00", "00:11:00")
        old_release = _release(repository, conn, "lease-a", "00:10:10")
        after_old_release = repository.mcp_session_by_id(
            transaction=conn, tenant_id="tenant-a", session_id="ontology-mcp-session-1"
        )
        released = _release(repository, conn, "lease-b", "00:10:20")
        current = repository.mcp_session_by_id(
            transaction=conn, tenant_id="tenant-a", session_id="ontology-mcp-session-1"
        )
        terminated = repository.terminate_mcp_session(
            transaction=conn,
            tenant_id="tenant-a",
            session_id="ontology-mcp-session-1",
            terminated_at=_timestamp("00:10:30"),
        )
        after_termination = _claim_stream(repository, conn, "lease-c", "00:11:00", "00:12:00")

    assert first is not None and first["stream_lease_id"] == "lease-a"
    assert blocked is None and stale_release is False
    assert reclaimed is not None and reclaimed["stream_lease_id"] == "lease-b"
    assert old_release is False and released is True
    assert after_old_release is not None and after_old_release["stream_lease_id"] == "lease-b"
    assert current is not None and current["stream_lease_id"] is None
    assert current["stream_lease_expires_at"] is None
    assert terminated is not None and terminated["status"] == "terminated"
    assert after_termination is None


def test_mcp_stream_lease_claim_is_atomic_across_connections(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp-stream-lease.db'}",
        future=True,
        connect_args={"timeout": 10},
    )
    db.metadata.create_all(engine)
    repository: OsdkApplicationRepository = SqlAlchemyOsdkApplicationRepository(engine)
    with engine.begin() as conn:
        repository.insert_mcp_session_or_get_existing(transaction=conn, record=_mcp_session())
    barrier = Barrier(2)

    def claim(lease_id: str) -> str | None:
        barrier.wait()
        with engine.begin() as conn:
            row = _claim_stream(repository, conn, lease_id, "00:09:00", "00:10:00")
        return row["stream_lease_id"] if row else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("lease-a", "lease-b")))

    assert sorted(value for value in results if value is not None) in (["lease-a"], ["lease-b"])


def _claim_stream(
    repository: OsdkApplicationRepository,
    transaction: TransactionContext,
    lease_id: str,
    claimed_time: str,
    expires_time: str,
) -> OsdkMcpSessionRow | None:
    return repository.claim_mcp_session_stream_lease(
        transaction=transaction,
        tenant_id="tenant-a",
        session_id="ontology-mcp-session-1",
        lease_id=lease_id,
        claimed_at=_timestamp(claimed_time),
        lease_expires_at=_timestamp(expires_time),
    )


def _release(
    repository: OsdkApplicationRepository,
    transaction: TransactionContext,
    lease_id: str,
    released_time: str,
) -> bool:
    return repository.release_mcp_session_stream_lease(
        transaction=transaction,
        tenant_id="tenant-a",
        session_id="ontology-mcp-session-1",
        lease_id=lease_id,
        released_at=_timestamp(released_time),
    )


def _mcp_session() -> OsdkMcpSessionRecord:
    return OsdkMcpSessionRecord(
        session_id="ontology-mcp-session-1",
        tenant_id="tenant-a",
        app_id="app-1",
        client_id="orders-web",
        actor_user_id="user-a",
        status="active",
        created_at="2026-07-01T00:08:00+00:00",
        last_seen_at="2026-07-01T00:08:00+00:00",
    )


def _timestamp(time_value: str) -> str:
    return f"2026-07-01T{time_value}+00:00"
