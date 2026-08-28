"""Application service helpers for processing workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.media_processing_ports import (
    ContentUnitRecord,
    MediaDerivativeRecord,
    MediaItemVersionRecord,
    MediaProcessingRequest,
    MediaProcessingResult,
    MediaProcessingRunRecord,
    MediaProcessorAdapter,
    MediaProcessorRegistry,
    ProcessorSpec,
)
from foundry_lite.application.media_source_materialization import (
    MediaByteVerificationFailure,
    materialized_verified_media,
    raise_media_byte_domain_error,
)
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.content_unit_paging import (
    _content_unit_limit,
    _require_non_negative,
    _require_positive_optional,
)
from foundry_lite.application.services.media.processing_records import (
    _canonical_spec_hash,
    _content_unit_records,
    _derivative_record,
    record_processing_failure_evidence_error,
)
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.application.services.media.read_access import (
    require_content_unit_clearance,
    require_media_derivative_clearance,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound


@dataclass(frozen=True)
class ProcessingOutcome:
    """Outcome of one processing run; ``status`` is COMMITTED only when DB-committed."""

    media_derivative_id: str
    status: str
    content_unit_count: int
    is_duplicate: bool = False
    error: dict[str, object] | None = None


@dataclass(frozen=True)
class ContentUnitPage:
    """Read-only page used by Document Intelligence and evidence viewers."""

    media_derivative_id: str
    source_media_item_version_id: str
    items: tuple[ContentUnitRecord, ...]
    next_cursor: int | None


class MediaProcessingService(CoreService):
    """Run a pinned processor over one COMMITTED media version and commit its derivative.

    Serving truth is the DB: the derivative + content units flip to COMMITTED atomically
    with audit + outbox in one transaction (doc §7 / invariant `workflow_status_does_not_
    replace_domain_commit`). A processor failure records a FAILED derivative for the
    operator; a commit failure rolls back so nothing is partially visible. Idempotency is
    the derivative's (source version + spec) unique key — a replay yields one derivative.
    """

    required_dependencies = (
        "engine",
        "policy",
        "media_repository",
        "media_derivative_repository",
        "media_storage",
        "media_source_workspace",
        "media_processor",
        "media_processor_registry",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def __init__(
        self,
        *,
        engine: object,
        policy: object,
        media_repository: object,
        media_derivative_repository: object,
        media_storage: object,
        media_source_workspace: object,
        media_processor_registry: MediaProcessorRegistry | None = None,
        media_processor: MediaProcessorAdapter | None = None,
    ) -> None:
        if media_processor_registry is None and media_processor is None:
            raise TypeError("MediaProcessingService requires a processor registry or legacy processor adapter")
        super().__init__(
            engine=engine,
            policy=policy,
            media_repository=media_repository,
            media_derivative_repository=media_derivative_repository,
            media_storage=media_storage,
            media_source_workspace=media_source_workspace,
            media_processor=media_processor,
            media_processor_registry=media_processor_registry,
        )

    def process(self, ctx: RequestContext, *, media_item_version_id: str, spec: ProcessorSpec) -> ProcessingOutcome:
        spec_hash = _canonical_spec_hash(spec)
        with self.engine.begin() as conn:
            version = self.media_repository.media_item_version_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=media_item_version_id
            )
        if version is None:
            raise NotFound("media version not found", details={"media_item_version_id": media_item_version_id})
        if version.status != "COMMITTED":
            raise ConflictDetected(
                "media version is not committed; only a serving-truth version can be processed",
                details={"media_item_version_id": media_item_version_id, "status": version.status},
            )
        envelope = dict(version.security_envelope)
        run_id = self._open_run(ctx, media_item_version_id, spec, spec_hash)
        # The run row is committed in its own txn above; any failure past this point (commit
        # CAS conflict, IntegrityError, outbox failure) must still land the run in a durable
        # FAILED verdict rather than leaving it stuck RUNNING forever with no reaper.
        try:
            result = self._run_processor(
                ctx,
                version,
                spec,
                spec_hash,
                media_item_version_id,
            )
            if isinstance(result, AdapterError):
                return self._record_failure(ctx, media_item_version_id, spec, spec_hash, envelope, result, run_id)
            return self._commit_derivative(ctx, media_item_version_id, spec, spec_hash, envelope, result, run_id)
        except Exception as exc:
            self._fail_open_run(ctx, run_id, exc)
            raise

    def list_media_runs(
        self, ctx: RequestContext, *, source_media_item_version_id: str | None = None, limit: int = 50
    ) -> list[MediaProcessingRunRecord]:
        """Operations surface: tenant-scoped processing runs newest-first (a failed attempt is visible)."""
        with self.engine.begin() as conn:
            return self.media_derivative_repository.list_media_runs(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                source_media_item_version_id=source_media_item_version_id,
                limit=limit,
            )

    def media_run_detail(self, ctx: RequestContext, *, media_processing_run_id: str) -> MediaProcessingRunRecord:
        with self.engine.begin() as conn:
            runs = self.media_derivative_repository.get_media_runs(transaction=conn, ids=[media_processing_run_id])
        run = next((candidate for candidate in runs if candidate.tenant_id == ctx.tenant_id), None)
        if run is None:
            raise NotFound(
                "media processing run not found", details={"media_processing_run_id": media_processing_run_id}
            )
        return run

    def _open_run(self, ctx: RequestContext, version_id: str, spec: ProcessorSpec, spec_hash: str) -> str:
        now = _now()
        record = MediaProcessingRunRecord(
            media_processing_run_id=_new_id("mrun"),
            tenant_id=ctx.tenant_id,
            source_media_item_version_id=version_id,
            processor_name=spec.processor,
            derivative_kind=spec.processor,
            processing_spec_hash=spec_hash,
            status="RUNNING",
            started_at=now,
            created_at=now,
        )
        with self.engine.begin() as conn:
            self.media_derivative_repository.create_media_run(transaction=conn, record=record)
        return record.media_processing_run_id

    def resolve_derivative(self, ctx: RequestContext, *, media_derivative_id: str) -> MediaDerivativeRecord:
        with self.engine.begin() as conn:
            derivative = self.media_derivative_repository.derivative_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_derivative_id=media_derivative_id
            )
        if derivative is None or derivative.status != "COMMITTED":
            raise NotFound(
                "committed derivative not found",
                details={"media_derivative_id": media_derivative_id},
            )
        return derivative

    def list_derivative_content_units(
        self,
        ctx: RequestContext,
        *,
        media_derivative_id: str,
        after_ordinal: int | None = None,
        page_number: int | None = None,
        limit: int = 200,
    ) -> ContentUnitPage:
        self.policy.require(ctx, "media:read")
        bounded_limit = _content_unit_limit(limit)
        _require_non_negative(after_ordinal, "afterOrdinal")
        _require_positive_optional(page_number, "pageNumber")
        with self.engine.begin() as conn:
            derivative = self.media_derivative_repository.derivative_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                media_derivative_id=media_derivative_id,
            )
            if derivative is None or derivative.status != "COMMITTED":
                raise NotFound("committed derivative not found", details={"media_derivative_id": media_derivative_id})
            require_media_derivative_clearance(ctx, derivative)
            rows = self.media_derivative_repository.get_content_units(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                derivative_id=media_derivative_id,
                after_ordinal=after_ordinal,
                page_number=page_number,
                limit=bounded_limit + 1,
            )
        for unit in rows:
            require_content_unit_clearance(ctx, unit)
        items = tuple(rows[:bounded_limit])
        next_cursor = items[-1].ordinal if len(rows) > bounded_limit and items else None
        return ContentUnitPage(media_derivative_id, derivative.source_media_item_version_id, items, next_cursor)

    def sweep_orphan_derivatives(self, ctx: RequestContext, *, older_than: str) -> list[str]:
        with self.engine.begin() as conn:
            orphans = self.media_derivative_repository.fetch_unreachable_staged_derivatives(
                transaction=conn, tenant_id=ctx.tenant_id, older_than=older_than
            )
            ids = [orphan.media_derivative_id for orphan in orphans]
            for orphan in orphans:
                if orphan.blob_key is not None:
                    self.media_storage.delete_uncommitted(orphan.blob_key)
            if ids:
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="media.derivative.swept",
                    resource_type="media_derivative",
                    resource_id=None,
                    action="purge",
                    after_ref={"derivativeIds": ids},
                    correlation_id=ctx.request_id,
                )
        return ids

    def _run_processor(
        self,
        ctx: RequestContext,
        version: MediaItemVersionRecord,
        spec: ProcessorSpec,
        spec_hash: str,
        version_id: str,
    ) -> MediaProcessingResult | AdapterError:
        try:
            processor = self._resolve_processor(spec, input_format=version.format)
        except AdapterError as err:
            return err
        try:
            workspace = materialized_verified_media(
                self.media_source_workspace, self.media_storage, version, file_name="source"
            )
            with workspace as source_path:
                request = MediaProcessingRequest(
                    tenant_id=ctx.tenant_id,
                    media_item_version_id=version_id,
                    blob_key=version.blob_key,
                    spec=spec,
                    processing_spec_hash=spec_hash,
                    source_path=source_path,
                    source_format=version.format,
                    source_mime_type=version.sniffed_mime_type,
                )
                if not processor.supports(request):
                    return _unsupported_processor_request(processor, request)
                try:
                    return processor.process(request)
                except AdapterError as err:
                    return err
                except Exception as err:
                    return _unexpected_processor_error(processor.profile_name, spec.processor, err)
        except MediaByteVerificationFailure as exc:
            raise_media_byte_domain_error(exc, operation="media_processing")

    def _resolve_processor(self, spec: ProcessorSpec, *, input_format: str) -> MediaProcessorAdapter:
        if self.media_processor_registry is not None:
            return self.media_processor_registry.resolve(spec, input_format=input_format)
        if self.media_processor is None:
            raise TypeError("legacy media processor adapter is unavailable")
        return self.media_processor

    def _commit_derivative(
        self,
        ctx: RequestContext,
        version_id: str,
        spec: ProcessorSpec,
        spec_hash: str,
        envelope: dict[str, object],
        result: MediaProcessingResult,
        run_id: str,
    ) -> ProcessingOutcome:
        record = _derivative_record(ctx, version_id, spec, spec_hash, envelope, result, status="STAGED")
        with self.engine.begin() as conn:
            existing = self.media_derivative_repository.create_derivative_or_get_existing(
                transaction=conn, record=record
            )
            if existing is not None and existing.status == "COMMITTED":
                self._finish_run(
                    conn, ctx, run_id, status="SUCCEEDED", media_derivative_id=existing.media_derivative_id
                )
                return ProcessingOutcome(existing.media_derivative_id, "committed", 0, is_duplicate=True)
            derivative_id = existing.media_derivative_id if existing is not None else record.media_derivative_id
            committed = self.media_derivative_repository.commit_derivative(
                transaction=conn, tenant_id=ctx.tenant_id, media_derivative_id=derivative_id, committed_at=_now()
            )
            if committed is None or committed.status != "COMMITTED":
                raise ConflictDetected("derivative commit lost its STAGED state", details={"id": derivative_id})
            units = _content_unit_records(ctx, version_id, derivative_id, spec_hash, envelope, result)
            self.media_derivative_repository.insert_content_units(transaction=conn, records=units)
            self._emit_events(conn, ctx, derivative_id, version_id, result)
            self._finish_run(conn, ctx, run_id, status="SUCCEEDED", media_derivative_id=derivative_id)
            return ProcessingOutcome(derivative_id, "committed", len(units), is_duplicate=existing is not None)

    def _record_failure(
        self,
        ctx: RequestContext,
        version_id: str,
        spec: ProcessorSpec,
        spec_hash: str,
        envelope: dict[str, object],
        error: AdapterError,
        run_id: str,
    ) -> ProcessingOutcome:
        payload = error.failure.to_payload()
        record = _derivative_record(ctx, version_id, spec, spec_hash, envelope, None, status="FAILED", error=payload)
        with self.engine.begin() as conn:
            existing = self.media_derivative_repository.create_derivative_or_get_existing(
                transaction=conn, record=record
            )
            derivative_id = existing.media_derivative_id if existing is not None else record.media_derivative_id
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.derivative.failed",
                resource_type="media_derivative",
                resource_id=derivative_id,
                action="process",
                decision="deny",
                after_ref={"error": payload},
                correlation_id=ctx.request_id,
            )
            self._finish_run(
                conn,
                ctx,
                run_id,
                status="FAILED",
                failure_kind=error.failure.kind,
                failure_reason=error.failure.operator_message,
            )
        return ProcessingOutcome(derivative_id, "failed", 0, error=payload)

    def _finish_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        *,
        status: str,
        media_derivative_id: str | None = None,
        failure_kind: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        # The run is operations evidence committed alongside the domain outcome; it never
        # substitutes for it (the COMMITTED derivative is the only serving truth).
        self.media_derivative_repository.complete_media_run(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            media_processing_run_id=run_id,
            status=status,
            finished_at=_now(),
            media_derivative_id=media_derivative_id,
            failure_kind=failure_kind,
            failure_reason=failure_reason,
        )

    def _fail_open_run(self, ctx: RequestContext, run_id: str, exc: Exception) -> None:
        # Durable safety net (mirrors _fail_seeded_run): a crash on the commit path rolled back
        # its own txn, so mark the run FAILED in a fresh txn. The CAS RUNNING->FAILED makes this
        # a no-op if the run already reached a verdict, and we never leak the raw error message
        # (it may carry secrets) — only the exception type is recorded as durable evidence.
        try:
            with self.engine.begin() as conn:
                self._finish_run(
                    conn,
                    ctx,
                    run_id,
                    status="FAILED",
                    failure_kind="unknown",
                    failure_reason=f"processing failed after run opened: {exc.__class__.__name__}",
                )
        except Exception as cleanup_error:
            record_processing_failure_evidence_error(exc, cleanup_error)

    def _emit_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        derivative_id: str,
        version_id: str,
        result: MediaProcessingResult,
    ) -> None:
        payload = {
            "mediaDerivativeId": derivative_id,
            "sourceMediaItemVersionId": version_id,
            "derivativeKind": result.derivative_kind,
            "contentUnitCount": len(result.units),
        }
        self.runtime_service._outbox(
            conn,
            ctx,
            "media.derivative.committed",
            "media_derivative",
            derivative_id,
            payload,
            idempotency_key=derivative_id,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="media.derivative.committed",
            resource_type="media_derivative",
            resource_id=derivative_id,
            action="process",
            after_ref=payload,
            correlation_id=ctx.request_id,
        )


def _unexpected_processor_error(adapter_profile: str, processor_name: str, err: Exception) -> AdapterError:
    failure = AdapterFailure(
        adapter_profile=adapter_profile,
        operation="process",
        kind="unknown",
        is_retryable=False,
        operator_message="unexpected media processor error",
        details={"processor": processor_name, "errorType": err.__class__.__name__},
    )
    return AdapterError(failure)


def _unsupported_processor_request(
    processor: MediaProcessorAdapter,
    request: MediaProcessingRequest,
) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile=processor.profile_name,
            operation="process",
            kind="unsupported",
            is_retryable=False,
            operator_message="selected media processor does not support the pinned request",
            details={
                "processor": request.spec.processor,
                "processorVersion": request.spec.processor_version,
                "inputFormat": request.source_format,
                "mediaItemVersionId": request.media_item_version_id,
            },
        )
    )
