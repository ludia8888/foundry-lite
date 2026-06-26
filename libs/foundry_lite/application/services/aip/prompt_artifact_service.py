"""Encrypted prompt artifact service."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from foundry_lite.application.ports.ai_run_repository import AiLedgerRow, AiPromptArtifactRecord
from foundry_lite.application.ports.prompt_artifact_store import (
    PromptArtifactBlob,
    PromptArtifactDelete,
    PromptArtifactRead,
    PromptArtifactStore,
    PromptArtifactWrite,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied

_PROMPT_ARTIFACT_READER_ROLE = "aip_prompt_artifact_reader"
_PROMPT_RETENTION_DAYS = 7
_EXPORT_MARKING = "aip-trace-restricted"


class PromptArtifactAccessDenied(PermissionDenied):
    """Raised when a caller lacks explicit raw prompt artifact access."""


class PromptArtifactNotFound(NotFound):
    """Raised when the tenant-scoped artifact receipt is missing."""


class PromptArtifactIntegrityError(ConflictDetected):
    """Raised when a prompt artifact no longer matches its ledger receipt."""


@dataclass(frozen=True)
class PromptArtifactReadResult:
    artifact_id: str
    ai_run_id: str
    content_hash: str
    encryption_key_ref: str
    export_marking: str
    plaintext: str


class PromptArtifactService(CoreService):
    """Write and explicitly gate access to encrypted raw prompt artifacts."""

    required_dependencies = ("engine", "ai_run_repository", "prompt_artifact_store")
    required_collaborators = ()
    prompt_artifact_store: PromptArtifactStore

    def record_compiled_prompt(
        self,
        ctx: RequestContext,
        *,
        ai_run_id: str,
        compiled_prompt_hash: str,
        compiled_prompt_text: str,
        created_at: str | None = None,
    ) -> AiPromptArtifactRecord:
        now = created_at or _now()
        artifact_id = f"{ai_run_id}-compiled-prompt"
        self._require_existing_run(ctx, ai_run_id)
        _require_content_hash(compiled_prompt_text, compiled_prompt_hash)
        blob = self.prompt_artifact_store.write_prompt_artifact(
            PromptArtifactWrite(
                tenant_id=ctx.tenant_id,
                ai_run_id=ai_run_id,
                artifact_id=artifact_id,
                plaintext=compiled_prompt_text,
            )
        )
        record = _artifact_record(ctx, ai_run_id, artifact_id, compiled_prompt_hash, blob, now)
        self._record_artifact_receipt_or_cleanup(ctx, record, blob)
        return record

    def _record_artifact_receipt_or_cleanup(
        self, ctx: RequestContext, record: AiPromptArtifactRecord, blob: PromptArtifactBlob
    ) -> None:
        try:
            with self.engine.begin() as transaction:
                self.ai_run_repository.record_prompt_artifact(transaction=transaction, record=record)
        except Exception:
            self.prompt_artifact_store.delete_prompt_artifact(
                PromptArtifactDelete(tenant_id=ctx.tenant_id, artifact_ref=blob.artifact_ref)
            )
            raise

    def _require_existing_run(self, ctx: RequestContext, ai_run_id: str) -> None:
        with self.engine.begin() as transaction:
            run = self.ai_run_repository.execution_run_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=ai_run_id
            )
        if run is None:
            raise PromptArtifactNotFound(f"AI run {ai_run_id} was not found")

    def read_prompt_artifact(
        self, ctx: RequestContext, *, artifact_id: str, ai_run_id: str | None = None
    ) -> PromptArtifactReadResult:
        if not ctx.has_role(_PROMPT_ARTIFACT_READER_ROLE):
            raise PromptArtifactAccessDenied("explicit prompt artifact reader role is required")
        with self.engine.begin() as transaction:
            row = self.ai_run_repository.prompt_artifact_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, artifact_id=artifact_id
            )
        if row is None:
            raise PromptArtifactNotFound(f"prompt artifact {artifact_id} was not found")
        if ai_run_id is not None and str(row["ai_run_id"]) != ai_run_id:
            raise PromptArtifactNotFound(f"prompt artifact {artifact_id} was not found for AI run {ai_run_id}")
        plaintext = self.prompt_artifact_store.read_prompt_artifact(
            PromptArtifactRead(
                tenant_id=ctx.tenant_id,
                artifact_ref=str(row["artifact_ref"]),
                encryption_key_ref=str(row["encryption_key_ref"]),
                expected_artifact_hash=str(row["artifact_hash"]),
                expected_content_hash=str(row["content_hash"]),
            )
        )
        return _read_result(row, plaintext)


def _artifact_record(
    ctx: RequestContext,
    ai_run_id: str,
    artifact_id: str,
    compiled_prompt_hash: str,
    blob: PromptArtifactBlob,
    now: str,
) -> AiPromptArtifactRecord:
    return AiPromptArtifactRecord(
        id=artifact_id,
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        artifact_kind="compiled_prompt",
        artifact_ref=blob.artifact_ref,
        content_hash=compiled_prompt_hash,
        artifact_hash=blob.artifact_hash,
        byte_size=blob.byte_size,
        encryption_key_ref=blob.encryption_key_ref,
        encryption_algorithm=blob.encryption_algorithm,
        retention_until=_retention_until(now),
        is_legal_hold=False,
        erasure_request_id=None,
        export_marking=_EXPORT_MARKING,
        created_at=now,
    )


def _read_result(row: AiLedgerRow, plaintext: str) -> PromptArtifactReadResult:
    mapping: Mapping[str, object] = row
    return PromptArtifactReadResult(
        artifact_id=str(mapping["id"]),
        ai_run_id=str(mapping["ai_run_id"]),
        content_hash=str(mapping["content_hash"]),
        encryption_key_ref=str(mapping["encryption_key_ref"]),
        export_marking=str(mapping["export_marking"]),
        plaintext=plaintext,
    )


def _retention_until(created_at: str) -> str:
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (parsed + timedelta(days=_PROMPT_RETENTION_DAYS)).isoformat()


def _require_content_hash(plaintext: str, expected_hash: str) -> None:
    actual_hash = f"sha256:{hashlib.sha256(plaintext.encode('utf-8')).hexdigest()}"
    if actual_hash != expected_hash:
        raise PromptArtifactIntegrityError("compiled prompt text does not match compiled_prompt_hash")
