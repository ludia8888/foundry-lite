"""Tests for encrypted prompt artifact service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.ai_run_repository import AiExecutionRunRecord, AiSessionRecord
from foundry_lite.application.ports.prompt_artifact_store import PromptArtifactStoreError
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.services.aip.prompt_artifact_service import (
    PromptArtifactAccessDenied,
    PromptArtifactIntegrityError,
    PromptArtifactService,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_prompt_artifact_store import LocalPromptArtifactStore
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from sqlalchemy import create_engine, select

_TENANT = "tenant-demo"
_RAW_PROMPT = "raw compiled prompt: supplier margin 42, customer private note"


@dataclass(frozen=True)
class _StaticSecretProvider:
    secret: SecretValue
    profile_name: str = "static-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str) -> SecretValue:
        assert name == self.secret.name
        return self.secret


def test_prompt_artifact_service_stores_encrypted_receipt_and_requires_reader_role(tmp_path: Path) -> None:
    service, engine = _service(tmp_path)
    _seed_run(engine)
    ctx = RequestContext(tenant_id=_TENANT, actor_user_id="ops-user", roles=("ops_manager",))

    record = service.record_compiled_prompt(
        ctx,
        ai_run_id="ai-run-1",
        compiled_prompt_hash=_hash_text(_RAW_PROMPT),
        compiled_prompt_text=_RAW_PROMPT,
        created_at="2026-06-26T00:00:00+00:00",
    )

    row = _artifact_row(engine, record.id)
    serialized = json.dumps(dict(row), sort_keys=True)
    assert row["artifact_kind"] == "compiled_prompt"
    assert row["content_hash"] == _hash_text(_RAW_PROMPT)
    assert row["artifact_hash"].startswith("sha256:")
    assert row["retention_until"] == "2026-07-03T00:00:00+00:00"
    assert row["legal_hold"] is False
    assert row["export_marking"] == "aip-trace-restricted"
    assert _RAW_PROMPT not in serialized
    assert _RAW_PROMPT.encode("utf-8") not in next(tmp_path.rglob("*.fernet")).read_bytes()
    with pytest.raises(PromptArtifactAccessDenied):
        service.read_prompt_artifact(ctx, artifact_id=record.id)

    reader_ctx = RequestContext(
        tenant_id=_TENANT,
        actor_user_id="auditor",
        roles=("ops_manager", "aip_prompt_artifact_reader"),
    )
    result = service.read_prompt_artifact(reader_ctx, artifact_id=record.id)

    assert result.plaintext == _RAW_PROMPT
    assert result.content_hash == _hash_text(_RAW_PROMPT)
    assert "aip_prompt_artifact_reader" not in serialized


def test_prompt_artifact_service_rejects_mismatched_compiled_prompt_hash(tmp_path: Path) -> None:
    service, engine = _service(tmp_path)
    _seed_run(engine)
    ctx = RequestContext(tenant_id=_TENANT, actor_user_id="ops-user", roles=("ops_manager",))

    with pytest.raises(PromptArtifactIntegrityError, match="compiled_prompt_hash"):
        service.record_compiled_prompt(
            ctx,
            ai_run_id="ai-run-1",
            compiled_prompt_hash="sha256:wrong",
            compiled_prompt_text=_RAW_PROMPT,
            created_at="2026-06-26T00:00:00+00:00",
        )

    assert list(tmp_path.rglob("*.fernet")) == []


def test_prompt_artifact_service_deletes_artifact_when_receipt_insert_fails(tmp_path: Path) -> None:
    service, engine = _service(tmp_path, repository_profile="receipt-fails")
    _seed_run(engine)
    ctx = RequestContext(tenant_id=_TENANT, actor_user_id="ops-user", roles=("ops_manager",))

    with pytest.raises(RuntimeError, match="receipt insert failed"):
        service.record_compiled_prompt(
            ctx,
            ai_run_id="ai-run-1",
            compiled_prompt_hash=_hash_text(_RAW_PROMPT),
            compiled_prompt_text=_RAW_PROMPT,
            created_at="2026-06-26T00:00:00+00:00",
        )

    assert list(tmp_path.rglob("*.fernet")) == []


def test_prompt_artifact_service_fails_closed_when_receipt_hash_does_not_match(tmp_path: Path) -> None:
    service, engine = _service(tmp_path)
    _seed_run(engine)
    ctx = RequestContext(tenant_id=_TENANT, actor_user_id="ops-user", roles=("ops_manager",))
    reader_ctx = RequestContext(
        tenant_id=_TENANT,
        actor_user_id="auditor",
        roles=("ops_manager", "aip_prompt_artifact_reader"),
    )
    record = service.record_compiled_prompt(
        ctx,
        ai_run_id="ai-run-1",
        compiled_prompt_hash=_hash_text(_RAW_PROMPT),
        compiled_prompt_text=_RAW_PROMPT,
        created_at="2026-06-26T00:00:00+00:00",
    )
    with engine.begin() as transaction:
        transaction.execute(
            db.ai_prompt_artifacts.update()
            .where(db.ai_prompt_artifacts.c.id == record.id)
            .values(content_hash=_hash_text("different prompt"))
        )

    with pytest.raises(PromptArtifactStoreError, match="content hash mismatch"):
        service.read_prompt_artifact(reader_ctx, artifact_id=record.id)


class _ReceiptFailingAiRunRepository:
    def __init__(self, delegate: SqlAlchemyAiRunRepository) -> None:
        self._delegate = delegate

    def execution_run_by_id(self, **kwargs: Any) -> Any:
        return self._delegate.execution_run_by_id(**kwargs)

    def record_prompt_artifact(self, **kwargs: Any) -> None:
        raise RuntimeError("receipt insert failed")


def _service(tmp_path: Path, *, repository_profile: str = "sql") -> tuple[PromptArtifactService, Any]:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.create_database(engine)
    repository = SqlAlchemyAiRunRepository(engine)
    secret_provider = _StaticSecretProvider(
        SecretValue(
            name="aip_prompt_artifact_encryption_key",
            version="sha256:test-key",
            value=Fernet.generate_key().decode("ascii"),
        )
    )
    store = LocalPromptArtifactStore(tmp_path, secret_provider, allow_local_dev_fallback=False)
    service_repository: Any = repository
    if repository_profile == "receipt-fails":
        service_repository = _ReceiptFailingAiRunRepository(repository)
    return PromptArtifactService(
        engine=engine, ai_run_repository=service_repository, prompt_artifact_store=store
    ), engine


def _seed_run(engine: Any) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with engine.begin() as transaction:
        repository.create_session(transaction=transaction, record=_session_record())
        repository.create_execution_run(transaction=transaction, record=_run_record())


def _artifact_row(engine: Any, artifact_id: str) -> Any:
    with engine.begin() as transaction:
        return (
            transaction.execute(select(db.ai_prompt_artifacts).where(db.ai_prompt_artifacts.c.id == artifact_id))
            .mappings()
            .one()
        )


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-1",
        tenant_id=_TENANT,
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        status="active",
        created_at="2026-06-26T00:00:00+00:00",
        last_activity_at="2026-06-26T00:00:00+00:00",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-1",
        tenant_id=_TENANT,
        session_id="ai-session-1",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        request_id="req-1",
        trace_id="trace-1",
        status="running",
        ontology_version_id="ontology-v1",
        model_alias_version="default-completion",
        resolved_model_id="local-fake-model",
        resolved_model_revision="fake-v1",
        prompt_version_id="prompt-v1",
        compiled_prompt_hash="sha256:compiled-prompt",
        tool_manifest_hash="sha256:tools",
        context_manifest_hash="sha256:context",
        state_snapshot_hash="sha256:state",
        policy_snapshot_hash="sha256:policy",
        budget_json={"maxTokens": 512},
        usage_json=None,
        error_json=None,
        started_at="2026-06-26T00:00:00+00:00",
        completed_at=None,
    )


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
