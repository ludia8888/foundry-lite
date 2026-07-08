"""SQLAlchemy repository adapter for source management repository persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.source_management_repository import (
    SourceAgentRecord,
    SourceAgentRow,
    SourceCredentialRecord,
    SourceCredentialRow,
    SourceExplorationRunRecord,
    SourceManagementAlreadyExistsError,
    SourceNetworkPolicyRecord,
    SourceNetworkPolicyRow,
    SourceSyncRecord,
    SourceSyncRow,
    SourceSyncRunRecord,
    SourceSyncRunRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.state_transitions import (
    SOURCE_AGENT_HEARTBEAT,
    SOURCE_SYNC_RUN_FAILED,
    SOURCE_SYNC_RUN_RUNNING,
    SOURCE_SYNC_RUN_SUCCEEDED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


class SqlAlchemySourceManagementRepository:
    """SQLAlchemy implementation of Source management records."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_credential(self, *, transaction: Any, record: SourceCredentialRecord) -> None:
        _insert_record(transaction, db.source_credentials, _credential_values(record))

    def credential_by_name(
        self, *, transaction: Any, tenant_id: str, credential_name: str
    ) -> SourceCredentialRow | None:
        row = _one_by_keys(transaction, db.source_credentials, tenant_id, credential_name=credential_name)
        return cast(SourceCredentialRow, row) if row else None

    def list_credentials(self, *, tenant_id: str) -> list[SourceCredentialRow]:
        return cast(
            list[SourceCredentialRow], _list_by_tenant(self.engine, db.source_credentials, tenant_id, "credential_name")
        )

    def create_agent(self, *, transaction: Any, record: SourceAgentRecord) -> None:
        _insert_record(transaction, db.source_agents, _agent_values(record))

    def agent_by_id(self, *, transaction: Any, tenant_id: str, agent_id: str) -> SourceAgentRow | None:
        row = _one_by_keys(transaction, db.source_agents, tenant_id, agent_id=agent_id)
        return cast(SourceAgentRow, row) if row else None

    def list_agents(self, *, tenant_id: str) -> list[SourceAgentRow]:
        return cast(list[SourceAgentRow], _list_by_tenant(self.engine, db.source_agents, tenant_id, "agent_id"))

    def update_agent_heartbeat(
        self, *, transaction: Any, tenant_id: str, agent_id: str, heartbeat_at: str
    ) -> SourceAgentRow | None:
        cas_status_update_many(
            transaction,
            db.source_agents,
            tenant_id=tenant_id,
            transition=SOURCE_AGENT_HEARTBEAT,
            values={"last_heartbeat_at": heartbeat_at, "updated_at": heartbeat_at},
            conditions=(db.source_agents.c.agent_id == agent_id,),
        )
        return self.agent_by_id(transaction=transaction, tenant_id=tenant_id, agent_id=agent_id)

    def create_network_policy(self, *, transaction: Any, record: SourceNetworkPolicyRecord) -> None:
        _insert_record(transaction, db.source_network_policies, _network_policy_values(record))

    def network_policy_by_name(
        self, *, transaction: Any, tenant_id: str, policy_name: str
    ) -> SourceNetworkPolicyRow | None:
        row = _one_by_keys(transaction, db.source_network_policies, tenant_id, policy_name=policy_name)
        return cast(SourceNetworkPolicyRow, row) if row else None

    def list_network_policies(self, *, tenant_id: str) -> list[SourceNetworkPolicyRow]:
        rows = _list_by_tenant(self.engine, db.source_network_policies, tenant_id, "policy_name")
        return cast(list[SourceNetworkPolicyRow], rows)

    def create_exploration_run(self, *, transaction: Any, record: SourceExplorationRunRecord) -> None:
        _insert_record(transaction, db.source_exploration_runs, _exploration_run_values(record))

    def create_sync(self, *, transaction: Any, record: SourceSyncRecord) -> None:
        _insert_record(transaction, db.source_syncs, _sync_values(record))

    def sync_by_name(self, *, transaction: Any, tenant_id: str, sync_name: str) -> SourceSyncRow | None:
        row = _one_by_keys(transaction, db.source_syncs, tenant_id, sync_name=sync_name)
        return cast(SourceSyncRow, row) if row else None

    def list_syncs(self, *, tenant_id: str) -> list[SourceSyncRow]:
        return cast(list[SourceSyncRow], _list_by_tenant(self.engine, db.source_syncs, tenant_id, "sync_name"))

    def update_sync_after_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        sync_name: str,
        run_id: str,
        workflow_run_id: str | None,
        checkpoint: Mapping[str, object],
        updated_at: str,
    ) -> SourceSyncRow | None:
        transaction.execute(
            update(db.source_syncs)
            .where(and_(db.source_syncs.c.tenant_id == tenant_id, db.source_syncs.c.sync_name == sync_name))
            .values(
                last_run_id=run_id, last_workflow_run_id=workflow_run_id, checkpoint=checkpoint, updated_at=updated_at
            )
        )
        return self.sync_by_name(transaction=transaction, tenant_id=tenant_id, sync_name=sync_name)

    def create_sync_run(self, *, transaction: Any, record: SourceSyncRunRecord) -> None:
        _insert_record(transaction, db.source_sync_runs, _sync_run_values(record))

    def sync_run_by_idempotency_key(
        self, *, transaction: Any, tenant_id: str, sync_name: str, idempotency_key: str
    ) -> SourceSyncRunRow | None:
        row = _one_by_keys(
            transaction,
            db.source_sync_runs,
            tenant_id,
            sync_name=sync_name,
            idempotency_key=idempotency_key,
        )
        return cast(SourceSyncRunRow, row) if row else None

    def update_sync_run_result(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        status: str,
        dataset_version_id: str | None,
        checkpoint_end: Mapping[str, object],
        result_summary: Mapping[str, object],
        error: Mapping[str, object] | None,
        completed_at: str,
        workflow_run_id: str | None = None,
    ) -> SourceSyncRunRow | None:
        result_values: dict[str, object | None] = {
            "dataset_version_id": dataset_version_id,
            "checkpoint_end": checkpoint_end,
            "result_summary": result_summary,
            "error": error,
            "completed_at": completed_at,
        }
        if workflow_run_id is not None:
            result_values["workflow_run_id"] = workflow_run_id
        cas_status_update(
            transaction,
            db.source_sync_runs,
            tenant_id=tenant_id,
            row_id=run_id,
            transition=_source_sync_run_transition(status),
            values=result_values,
        )
        return self.sync_run_by_id(transaction=transaction, tenant_id=tenant_id, run_id=run_id)

    def sync_run_by_id(self, *, transaction: Any, tenant_id: str, run_id: str) -> SourceSyncRunRow | None:
        row = _one_by_keys(transaction, db.source_sync_runs, tenant_id, id=run_id)
        return cast(SourceSyncRunRow, row) if row else None

    def list_sync_runs(self, *, tenant_id: str, sync_name: str) -> list[SourceSyncRunRow]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.source_sync_runs)
                    .where(
                        and_(db.source_sync_runs.c.tenant_id == tenant_id, db.source_sync_runs.c.sync_name == sync_name)
                    )
                    .order_by(db.source_sync_runs.c.created_at.desc())
                )
                .mappings()
                .all()
            )
        return [cast(SourceSyncRunRow, dict(row)) for row in rows]


def _insert_record(transaction: Any, table: Any, values: dict[str, object]) -> None:
    try:
        transaction.execute(insert(table).values(**values))
    except IntegrityError as exc:
        raise SourceManagementAlreadyExistsError from exc


def _source_sync_run_transition(status: str) -> StatusTransition:
    transitions = {
        "running": SOURCE_SYNC_RUN_RUNNING,
        "succeeded": SOURCE_SYNC_RUN_SUCCEEDED,
        "failed": SOURCE_SYNC_RUN_FAILED,
    }
    return transitions[status]


def _one_by_keys(transaction: Any, table: Any, tenant_id: str, **keys: object) -> dict[str, object] | None:
    clauses = [table.c.tenant_id == tenant_id, *[getattr(table.c, key) == value for key, value in keys.items()]]
    row = transaction.execute(select(table).where(and_(*clauses))).mappings().first()
    return dict(row) if row else None


def _list_by_tenant(engine: Engine, table: Any, tenant_id: str, order_column: str) -> list[dict[str, object]]:
    with engine.begin() as conn:
        rows = (
            conn.execute(select(table).where(table.c.tenant_id == tenant_id).order_by(getattr(table.c, order_column)))
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _credential_values(record: SourceCredentialRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "credential_name": record.credential_name,
        "display_name": record.display_name,
        "kind": record.kind,
        "auth_scheme": record.auth_scheme,
        "secret_name": record.secret_name,
        "secret_version": record.secret_version,
        "status": record.status,
        "config_summary": dict(record.config_summary),
        "config_fingerprint": record.config_fingerprint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _agent_values(record: SourceAgentRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "agent_id": record.agent_id,
        "display_name": record.display_name,
        "mode": record.mode,
        "status": record.status,
        "capabilities": dict(record.capabilities),
        "network_summary": dict(record.network_summary),
        "last_heartbeat_at": record.last_heartbeat_at,
        "config_fingerprint": record.config_fingerprint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _network_policy_values(record: SourceNetworkPolicyRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "policy_name": record.policy_name,
        "display_name": record.display_name,
        "mode": record.mode,
        "agent_id": record.agent_id,
        "allowed_hosts": dict(record.allowed_hosts),
        "status": record.status,
        "config_fingerprint": record.config_fingerprint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _exploration_run_values(record: SourceExplorationRunRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "source_name": record.source_name,
        "source_type": record.source_type,
        "request": dict(record.request),
        "status": record.status,
        "result_summary": dict(record.result_summary),
        "error": dict(record.error) if record.error else None,
        "operations_path": record.operations_path,
        "created_at": record.created_at,
    }


def _sync_values(record: SourceSyncRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "sync_name": record.sync_name,
        "source_name": record.source_name,
        "display_name": record.display_name,
        "source_type": record.source_type,
        "capability": record.capability,
        "target_dataset_ref": record.target_dataset_ref,
        "target_media_set_id": record.target_media_set_id,
        "mode": record.mode,
        "schedule": dict(record.schedule),
        "config_summary": dict(record.config_summary),
        "config_fingerprint": record.config_fingerprint,
        "status": record.status,
        "last_run_id": record.last_run_id,
        "last_workflow_run_id": record.last_workflow_run_id,
        "checkpoint": dict(record.checkpoint),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _sync_run_values(record: SourceSyncRunRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "sync_name": record.sync_name,
        "source_name": record.source_name,
        "source_type": record.source_type,
        "capability": record.capability,
        "workflow_run_id": record.workflow_run_id,
        "dataset_version_id": record.dataset_version_id,
        "status": record.status,
        "trigger_type": record.trigger_type,
        "idempotency_key": record.idempotency_key,
        "batch_limit": record.batch_limit,
        "checkpoint_start": dict(record.checkpoint_start),
        "checkpoint_end": dict(record.checkpoint_end),
        "result_summary": dict(record.result_summary),
        "error": dict(record.error) if record.error else None,
        "operations_path": record.operations_path,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "created_at": record.created_at,
    }
