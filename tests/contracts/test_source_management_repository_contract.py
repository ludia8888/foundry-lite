from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.source_management_repository import (
    SourceAgentRecord,
    SourceCredentialRecord,
    SourceManagementRepository,
    SourceNetworkPolicyRecord,
    SourceSyncRecord,
    SourceSyncRunRecord,
)
from foundry_lite.application.state_transitions import SOURCE_SYNC_SCHEDULE_PAUSED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemySourceManagementRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'source_management.db'}", future=True)
    db.create_database(engine)
    return engine


@pytest.fixture
def repository(engine: Engine) -> SourceManagementRepository:
    return SqlAlchemySourceManagementRepository(engine)


def test_source_management_credentials_are_tenant_scoped(
    engine: Engine, repository: SourceManagementRepository
) -> None:
    with engine.begin() as conn:
        repository.create_credential(transaction=conn, record=_credential("tenant-a", "erp_token"))
        repository.create_credential(transaction=conn, record=_credential("tenant-b", "erp_token"))
        tenant_a = repository.credential_by_name(transaction=conn, tenant_id="tenant-a", credential_name="erp_token")
        tenant_b = repository.credential_by_name(transaction=conn, tenant_id="tenant-b", credential_name="erp_token")

    assert tenant_a is not None
    assert tenant_a["tenant_id"] == "tenant-a"
    assert tenant_b is not None
    assert tenant_b["tenant_id"] == "tenant-b"
    assert [row["tenant_id"] for row in repository.list_credentials(tenant_id="tenant-a")] == ["tenant-a"]


def test_source_management_agent_heartbeat_uses_status_transition(
    engine: Engine, repository: SourceManagementRepository
) -> None:
    with engine.begin() as conn:
        repository.create_agent(transaction=conn, record=_agent("tenant-a", "agent-1"))
        updated = repository.update_agent_heartbeat(
            transaction=conn,
            tenant_id="tenant-a",
            agent_id="agent-1",
            heartbeat_at="2026-06-28T00:01:00Z",
        )

    assert updated is not None
    assert updated["status"] == "online"
    assert updated["last_heartbeat_at"] == "2026-06-28T00:01:00Z"


def test_source_management_policy_and_sync_run_lifecycle(
    engine: Engine, repository: SourceManagementRepository
) -> None:
    with engine.begin() as conn:
        repository.create_network_policy(transaction=conn, record=_network_policy("tenant-a", "private_proxy"))
        repository.create_sync(transaction=conn, record=_sync("tenant-a", "orders_sync"))
        scheduled = repository.update_sync_schedule(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            schedule={"mode": "interval", "everySeconds": 3600},
            config_fingerprint="sha256:tenant-a:scheduled-sync",
            updated_at="2026-06-28T00:00:30Z",
        )
        stale_pause = repository.update_sync_schedule_state(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            transition=SOURCE_SYNC_SCHEDULE_PAUSED,
            expected_config_fingerprint="sha256:tenant-a:stale-sync",
            schedule={"mode": "interval", "everySeconds": 3600},
            config_fingerprint="sha256:tenant-a:should-not-commit",
            updated_at="2026-06-28T00:00:35Z",
        )
        paused = repository.update_sync_schedule_state(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            transition=SOURCE_SYNC_SCHEDULE_PAUSED,
            expected_config_fingerprint="sha256:tenant-a:scheduled-sync",
            schedule={"mode": "interval", "everySeconds": 3600},
            config_fingerprint="sha256:tenant-a:paused-sync",
            updated_at="2026-06-28T00:00:40Z",
        )
        repository.create_sync_run(transaction=conn, record=_sync_run("tenant-a", "orders_sync", "run-1", "idem-1"))
        repository.create_sync_run(
            transaction=conn,
            record=_sync_run("tenant-a", "orders_sync", "run-cancelled", "idem-cancelled"),
        )
        replay = repository.sync_run_by_idempotency_key(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            idempotency_key="idem-1",
        )
        completed = repository.update_sync_run_result(
            transaction=conn,
            tenant_id="tenant-a",
            run_id="run-1",
            status="succeeded",
            dataset_version_id="version-1",
            checkpoint_end={"lastValue": 20},
            result_summary={"rowCount": 2},
            error=None,
            completed_at="2026-06-28T00:02:00Z",
        )
        cancelled = repository.update_sync_run_result(
            transaction=conn,
            tenant_id="tenant-a",
            run_id="run-cancelled",
            status="cancelled",
            dataset_version_id=None,
            checkpoint_end={},
            result_summary={"workflowOutput": {"cleanup": {"status": "adapter_confirmed"}}},
            error=None,
            completed_at="2026-06-28T00:02:30Z",
        )
        sync_after = repository.update_sync_after_run(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            run_id="run-1",
            workflow_run_id=None,
            checkpoint={"lastValue": 20},
            updated_at="2026-06-28T00:02:00Z",
        )
        streaming = repository.update_sync_streaming_workflow(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            workflow_run_id="workflow-stream-1",
            updated_at="2026-06-28T00:03:00Z",
        )
        after_microbatch = repository.update_sync_after_run(
            transaction=conn,
            tenant_id="tenant-a",
            sync_name="orders_sync",
            run_id="run-2",
            workflow_run_id=None,
            checkpoint={"lastValue": 30},
            updated_at="2026-06-28T00:04:00Z",
        )
        policy = repository.network_policy_by_name(
            transaction=conn,
            tenant_id="tenant-a",
            policy_name="private_proxy",
        )

    assert replay is not None
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] is None
    assert scheduled is not None
    assert stale_pause is None
    assert scheduled["schedule"] == {"mode": "interval", "everySeconds": 3600}
    assert scheduled["config_fingerprint"] == "sha256:tenant-a:scheduled-sync"
    assert paused is not None
    assert paused["status"] == "paused"
    assert paused["schedule"] == {"mode": "interval", "everySeconds": 3600}
    assert replay["id"] == "run-1"
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert completed["dataset_version_id"] == "version-1"
    assert completed["checkpoint_end"] == {"lastValue": 20}
    assert sync_after is not None
    assert sync_after["last_run_id"] == "run-1"
    assert sync_after["checkpoint"] == {"lastValue": 20}
    assert streaming is not None
    assert streaming["last_workflow_run_id"] == "workflow-stream-1"
    assert after_microbatch is not None
    assert after_microbatch["last_workflow_run_id"] == "workflow-stream-1"
    assert after_microbatch["checkpoint"] == {"lastValue": 30}
    assert policy is not None
    assert policy["agent_id"] == "agent-1"
    assert [row["id"] for row in repository.list_sync_runs(tenant_id="tenant-a", sync_name="orders_sync")] == [
        "run-1",
        "run-cancelled",
    ]


def _credential(tenant_id: str, credential_name: str) -> SourceCredentialRecord:
    return SourceCredentialRecord(
        id=f"cred-{tenant_id}",
        tenant_id=tenant_id,
        credential_name=credential_name,
        display_name="ERP Token",
        kind="api_token",
        auth_scheme="bearer",
        secret_name=f"{credential_name}_secret",
        secret_version="sha256:credential",
        status="active",
        config_summary={"kind": "api_token"},
        config_fingerprint=f"sha256:{tenant_id}:credential",
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _agent(tenant_id: str, agent_id: str) -> SourceAgentRecord:
    return SourceAgentRecord(
        id=f"source-agent-{tenant_id}",
        tenant_id=tenant_id,
        agent_id=agent_id,
        display_name="Private Network Agent",
        mode="agent_proxy",
        status="registered",
        capabilities={"protocols": ["jdbc"]},
        network_summary={"region": "local"},
        last_heartbeat_at=None,
        config_fingerprint=f"sha256:{tenant_id}:agent",
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _network_policy(tenant_id: str, policy_name: str) -> SourceNetworkPolicyRecord:
    return SourceNetworkPolicyRecord(
        id=f"policy-{tenant_id}",
        tenant_id=tenant_id,
        policy_name=policy_name,
        display_name="Private Proxy",
        mode="agent_proxy",
        agent_id="agent-1",
        allowed_hosts={"hosts": ["db.internal"]},
        status="active",
        config_fingerprint=f"sha256:{tenant_id}:policy",
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _sync(tenant_id: str, sync_name: str) -> SourceSyncRecord:
    return SourceSyncRecord(
        id=f"sync-{tenant_id}",
        tenant_id=tenant_id,
        sync_name=sync_name,
        source_name="orders_db",
        display_name="Orders Sync",
        source_type="postgres_jdbc",
        capability="table_batch",
        target_dataset_ref="raw.orders",
        target_media_set_id=None,
        mode="APPEND",
        schedule={"mode": "manual"},
        config_summary={"tableName": "orders"},
        config_fingerprint=f"sha256:{tenant_id}:sync",
        status="active",
        last_run_id=None,
        last_workflow_run_id=None,
        checkpoint={},
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _sync_run(
    tenant_id: str,
    sync_name: str,
    run_id: str,
    idempotency_key: str,
) -> SourceSyncRunRecord:
    return SourceSyncRunRecord(
        id=run_id,
        tenant_id=tenant_id,
        sync_name=sync_name,
        source_name="orders_db",
        source_type="postgres_jdbc",
        capability="table_batch",
        workflow_run_id=None,
        dataset_version_id=None,
        status="running",
        trigger_type="manual",
        idempotency_key=idempotency_key,
        batch_limit=100,
        checkpoint_start={},
        checkpoint_end={},
        result_summary={},
        error=None,
        operations_path=f"/api/operations/source-sync-runs/{run_id}",
        started_at="2026-06-28T00:01:00Z",
        completed_at=None,
        created_at="2026-06-28T00:01:00Z",
    )
