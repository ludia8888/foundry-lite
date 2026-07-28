"""Atomic committed Content Unit chunking for the Media and Content plane."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_derivative_repository import (
    ContentUnitRecord,
    MediaDerivativeRecord,
    MediaProcessingRunRecord,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.content_chunking_records import (
    chunk_drafts,
    chunk_outcome,
    commit_payload,
    content_unit_records,
    derivative_record,
    input_set_hash,
    processing_spec_hash,
    require_chunk_drafts,
    require_committed_derivative,
    require_replay_units,
    require_same_input_set,
    safe_failure_kind,
    source_unit_order,
    unit_signatures,
    validate_source_chain,
)
from foundry_lite.application.services.media.content_chunking_rules import (
    CONTENT_CHUNK_DERIVATIVE_KIND,
    CONTENT_CHUNK_PROCESSOR,
    ContentChunkSpec,
    content_chunk_config_hash,
    validate_content_chunk_spec,
)
from foundry_lite.application.services.media.content_chunking_types import (
    ChunkCommit as _ChunkCommit,
)
from foundry_lite.application.services.media.content_chunking_types import (
    CommittedContentUnitSetRef,
    ContentChunkOutcome,
)
from foundry_lite.application.services.media.content_chunking_types import (
    SourceContentUnitSet as _SourceContentUnitSet,
)
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

__all__ = [
    "CommittedContentUnitSetRef",
    "ContentChunkOutcome",
    "ContentChunkSpec",
    "ContentUnitChunkingService",
]


class ContentUnitChunkingService(CoreService):
    """Create one immutable chunk derivative from committed Content Units."""

    required_dependencies = ("engine", "media_repository", "media_derivative_repository")
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def create_chunks(
        self,
        ctx: RequestContext,
        *,
        source: CommittedContentUnitSetRef,
        spec: ContentChunkSpec | None = None,
    ) -> ContentChunkOutcome:
        selected_spec = spec if spec is not None else ContentChunkSpec()
        validate_content_chunk_spec(selected_spec)
        source_set = self._load_source(ctx, source)
        config_hash = content_chunk_config_hash(selected_spec)
        chunk_spec_hash = processing_spec_hash(config_hash, source_set.input_set_hash)
        run_id = self._open_run(ctx, source_set, chunk_spec_hash)
        try:
            drafts = chunk_drafts(source_set.units, selected_spec)
            require_chunk_drafts(drafts, source.media_derivative_id)
            command = _ChunkCommit(
                source_ref=source,
                source=source_set,
                spec=selected_spec,
                chunk_config_hash=config_hash,
                chunk_spec_hash=chunk_spec_hash,
                drafts=drafts,
                run_id=run_id,
            )
            return self._commit_chunks(ctx, command)
        except Exception as exc:
            self._fail_open_run(ctx, run_id, exc)
            raise

    def _load_source(
        self,
        ctx: RequestContext,
        source: CommittedContentUnitSetRef,
    ) -> _SourceContentUnitSet:
        with self.engine.begin() as transaction:
            return self._load_source_in_transaction(transaction, ctx, source)

    def _load_source_in_transaction(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        source: CommittedContentUnitSetRef,
    ) -> _SourceContentUnitSet:
        derivative = self.media_derivative_repository.derivative_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_derivative_id=source.media_derivative_id,
        )
        require_committed_derivative(derivative, source.media_derivative_id)
        assert derivative is not None
        version = self.media_repository.media_item_version_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_item_version_id=derivative.source_media_item_version_id,
        )
        units = self.media_derivative_repository.get_content_units(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            derivative_id=source.media_derivative_id,
        )
        ordered = tuple(sorted(units, key=source_unit_order))
        validate_source_chain(ctx, derivative, version, ordered)
        return _SourceContentUnitSet(derivative, ordered, input_set_hash(derivative, ordered))

    def _open_run(
        self,
        ctx: RequestContext,
        source: _SourceContentUnitSet,
        chunk_spec_hash: str,
    ) -> str:
        now = _now()
        run = MediaProcessingRunRecord(
            media_processing_run_id=_new_id("mrun"),
            tenant_id=ctx.tenant_id,
            source_media_item_version_id=source.derivative.source_media_item_version_id,
            processor_name=CONTENT_CHUNK_PROCESSOR,
            derivative_kind=CONTENT_CHUNK_DERIVATIVE_KIND,
            processing_spec_hash=chunk_spec_hash,
            status="RUNNING",
            started_at=now,
            created_at=now,
        )
        with self.engine.begin() as transaction:
            self.media_derivative_repository.create_media_run(transaction=transaction, record=run)
            self._audit_run_started(transaction, ctx, run, source.derivative.media_derivative_id)
        return run.media_processing_run_id

    def _commit_chunks(self, ctx: RequestContext, command: _ChunkCommit) -> ContentChunkOutcome:
        with self.engine.begin() as transaction:
            current = self._load_source_in_transaction(transaction, ctx, command.source_ref)
            require_same_input_set(command.source, current)
            record = derivative_record(ctx, current, command)
            existing = self.media_derivative_repository.create_derivative_or_get_existing(
                transaction=transaction,
                record=record,
            )
            if existing is not None and existing.status == "COMMITTED":
                return self._replay_outcome(transaction, ctx, command, existing)
            derivative_id = existing.media_derivative_id if existing is not None else record.media_derivative_id
            records = content_unit_records(ctx, derivative_id, current, command)
            self.media_derivative_repository.insert_content_units(transaction=transaction, records=records)
            persisted = self._require_persisted_units(transaction, ctx, derivative_id, records)
            self._commit_derivative(transaction, ctx, derivative_id)
            self._emit_commit_evidence(transaction, ctx, command, derivative_id, len(persisted))
            self._finish_run(transaction, ctx, command.run_id, "SUCCEEDED", derivative_id)
            return chunk_outcome(command, current, derivative_id, persisted, existing is not None)

    def _replay_outcome(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        command: _ChunkCommit,
        derivative: MediaDerivativeRecord,
    ) -> ContentChunkOutcome:
        units = self.media_derivative_repository.get_content_units(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            derivative_id=derivative.media_derivative_id,
        )
        expected = content_unit_records(ctx, derivative.media_derivative_id, command.source, command)
        require_replay_units(units, expected, derivative.media_derivative_id)
        self._finish_run(transaction, ctx, command.run_id, "SUCCEEDED", derivative.media_derivative_id)
        return chunk_outcome(command, command.source, derivative.media_derivative_id, units, True)

    def _require_persisted_units(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        derivative_id: str,
        expected: list[ContentUnitRecord],
    ) -> list[ContentUnitRecord]:
        persisted = self.media_derivative_repository.get_content_units(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            derivative_id=derivative_id,
        )
        if unit_signatures(persisted) != unit_signatures(expected):
            raise ConflictDetected(
                "content chunk persistence does not match the deterministic output",
                details={"mediaDerivativeId": derivative_id},
            )
        return persisted

    def _commit_derivative(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        derivative_id: str,
    ) -> None:
        committed = self.media_derivative_repository.commit_derivative(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_derivative_id=derivative_id,
            committed_at=_now(),
        )
        if committed is None or committed.status != "COMMITTED":
            raise ConflictDetected(
                "content chunk derivative commit lost its STAGED state",
                details={"mediaDerivativeId": derivative_id},
            )

    def _finish_run(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        status: str,
        derivative_id: str | None = None,
        *,
        failure_kind: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self.media_derivative_repository.complete_media_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_processing_run_id=run_id,
            status=status,
            finished_at=_now(),
            media_derivative_id=derivative_id,
            failure_kind=failure_kind,
            failure_reason=failure_reason,
        )

    def _fail_open_run(self, ctx: RequestContext, run_id: str, exc: Exception) -> None:
        failure_kind = safe_failure_kind(exc)
        try:
            with self.engine.begin() as transaction:
                self._finish_run(
                    transaction,
                    ctx,
                    run_id,
                    "FAILED",
                    failure_kind=failure_kind,
                    failure_reason=f"content chunking failed: {failure_kind}",
                )
        except Exception:
            return

    def _audit_run_started(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        run: MediaProcessingRunRecord,
        source_derivative_id: str,
    ) -> None:
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="media.content_chunk.run_started",
            resource_type="media_processing_run",
            resource_id=run.media_processing_run_id,
            action="chunk",
            after_ref={
                "sourceMediaDerivativeId": source_derivative_id,
                "sourceMediaItemVersionId": run.source_media_item_version_id,
                "chunkSpecHash": run.processing_spec_hash,
            },
            correlation_id=ctx.request_id,
        )

    def _emit_commit_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        command: _ChunkCommit,
        derivative_id: str,
        content_unit_count: int,
    ) -> None:
        payload = commit_payload(command, derivative_id, content_unit_count)
        self.runtime_service._outbox(
            transaction,
            ctx,
            "media.derivative.committed",
            "media_derivative",
            derivative_id,
            payload,
            idempotency_key=derivative_id,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="media.derivative.committed",
            resource_type="media_derivative",
            resource_id=derivative_id,
            action="chunk",
            after_ref=payload,
            correlation_id=ctx.request_id,
        )
