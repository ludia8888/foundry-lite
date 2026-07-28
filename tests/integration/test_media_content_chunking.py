"""Committed Content Unit set -> atomic chunk derivative integration proof."""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.content_chunking import (
    CommittedContentUnitSetRef,
    ContentUnitChunkingService,
)
from foundry_lite.application.services.media.content_chunking_rules import ContentChunkSpec
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from sqlalchemy import and_, create_engine, func, select, update
from sqlalchemy.engine import Engine

_SECURITY = {
    "tenantId": "tenant-demo",
    "classification": "confidential",
    "policyVersion": "policy-v1",
}
_SOURCE_DERIVATIVE_ID = "mder-layout-source"
_ALTERNATE_DERIVATIVE_ID = "mder-ocr-source"
_HEADING_ID = "cu-layout-heading"
_BODY_ID = "cu-layout-body"


class _Runtime:
    def __init__(self) -> None:
        self.fail_chunk_outbox = False
        self.audits: list[tuple[str, dict[str, object]]] = []
        self.outbox: list[tuple[str, dict[str, object]]] = []

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        after_ref: Mapping[str, object] | None = None,
        **_: object,
    ) -> None:
        del conn, ctx
        self.audits.append((event_type, dict(after_ref or {})))

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        **_: object,
    ) -> str:
        del conn, ctx, aggregate_type, aggregate_id
        materialized = dict(payload)
        if self.fail_chunk_outbox and materialized.get("processorName") == "content_chunk_v1":
            raise RuntimeError("injected chunk outbox failure")
        self.outbox.append((event_type, materialized))
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: Engine
    derivative_repository: SqlAlchemyMediaDerivativeRepository
    runtime: _Runtime
    chunking: ContentUnitChunkingService
    source: CommittedContentUnitSetRef
    version_id: str


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'content-chunking.db'}", future=True)
    db.create_database(engine)
    media_repository = SqlAlchemyMediaRepository(engine)
    derivative_repository = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _Runtime()
    ctx = RequestContext(tenant_id="tenant-demo")
    version_id = _commit_source_media(engine, media_repository, storage, runtime, ctx)
    _commit_layout_derivative(engine, derivative_repository, ctx, version_id)
    chunking = ContentUnitChunkingService(
        engine=engine,
        media_repository=media_repository,
        media_derivative_repository=derivative_repository,
    )
    chunking.bind_collaborators({"runtime_service": runtime})
    return _Env(
        ctx=ctx,
        engine=engine,
        derivative_repository=derivative_repository,
        runtime=runtime,
        chunking=chunking,
        source=CommittedContentUnitSetRef(_SOURCE_DERIVATIVE_ID),
        version_id=version_id,
    )


def test_chunk_commit_preserves_parent_coordinates_structure_and_security(env: _Env) -> None:
    outcome = env.chunking.create_chunks(env.ctx, source=env.source, spec=ContentChunkSpec(4, 1))

    assert outcome.status == "COMMITTED"
    assert outcome.content_unit_count == 3
    derivative, units = _read_output(env, outcome.media_derivative_id)
    assert derivative.processor_name == "content_chunk_v1"
    assert derivative.processor_version == "1.0.0"
    assert derivative.security_envelope == _SECURITY
    assert [unit.text for unit in units] == [
        "alpha beta gamma delta",
        "delta epsilon zeta",
        "eta theta iota",
    ]
    assert [unit.parent_content_unit_id for unit in units] == [_HEADING_ID, _HEADING_ID, _BODY_ID]
    assert units[0].bbox == _heading_bbox()
    assert units[0].source_locator == _heading_locator()
    assert units[0].security_envelope == _SECURITY
    assert units[0].structure is not None and units[0].structure["kind"] == "heading"
    assert units[0].structure["level"] == 1
    assert units[0].structure["foundryContentChunk"] == {
        "localOrdinal": 0,
        "startToken": 0,
        "endToken": 4,
        "chunkSize": 4,
        "overlap": 1,
        "tokenizerVersion": "whitespace_v1",
    }
    assert units[0].embedding == ()
    _assert_commit_evidence_has_no_raw_text(env, outcome.media_derivative_id)


def test_same_input_and_spec_replay_one_derivative_and_one_chunk_set(env: _Env) -> None:
    spec = ContentChunkSpec(4, 1)
    first = env.chunking.create_chunks(env.ctx, source=env.source, spec=spec)
    second = env.chunking.create_chunks(env.ctx, source=env.source, spec=spec)

    assert second.is_duplicate is True
    assert second.media_derivative_id == first.media_derivative_id
    assert second.content_unit_ids == first.content_unit_ids
    assert (
        _row_count(env.engine, db.media_derivatives, db.media_derivatives.c.processor_name == "content_chunk_v1") == 1
    )
    assert _row_count(env.engine, db.content_units, db.content_units.c.unit_kind == "chunk") == 3
    chunk_events = [payload for _, payload in env.runtime.outbox if payload.get("processorName") == "content_chunk_v1"]
    assert len(chunk_events) == 1
    assert _run_statuses(env) == ["SUCCEEDED", "SUCCEEDED"]


def test_different_chunk_config_creates_a_distinct_committed_derivative(env: _Env) -> None:
    first = env.chunking.create_chunks(env.ctx, source=env.source, spec=ContentChunkSpec(4, 1))
    second = env.chunking.create_chunks(env.ctx, source=env.source, spec=ContentChunkSpec(4, 2))

    assert second.media_derivative_id != first.media_derivative_id
    assert second.chunk_spec_hash != first.chunk_spec_hash
    assert second.chunk_config_hash != first.chunk_config_hash
    assert (
        _row_count(env.engine, db.media_derivatives, db.media_derivatives.c.processor_name == "content_chunk_v1") == 2
    )


def test_same_media_version_and_config_keep_different_extraction_inputs_distinct(env: _Env) -> None:
    _commit_alternate_derivative(env)
    spec = ContentChunkSpec(4, 1)

    layout = env.chunking.create_chunks(env.ctx, source=env.source, spec=spec)
    ocr = env.chunking.create_chunks(
        env.ctx,
        source=CommittedContentUnitSetRef(_ALTERNATE_DERIVATIVE_ID),
        spec=spec,
    )

    assert ocr.media_derivative_id != layout.media_derivative_id
    assert ocr.chunk_config_hash == layout.chunk_config_hash
    assert ocr.chunk_spec_hash != layout.chunk_spec_hash
    assert (
        _row_count(env.engine, db.media_derivatives, db.media_derivatives.c.processor_name == "content_chunk_v1") == 2
    )


def test_outbox_failure_rolls_back_derivative_and_chunks_and_marks_run_failed(env: _Env) -> None:
    env.runtime.fail_chunk_outbox = True
    with pytest.raises(RuntimeError, match="injected chunk outbox failure"):
        env.chunking.create_chunks(env.ctx, source=env.source, spec=ContentChunkSpec(3, 1))

    assert (
        _row_count(env.engine, db.media_derivatives, db.media_derivatives.c.processor_name == "content_chunk_v1") == 0
    )
    assert _row_count(env.engine, db.content_units, db.content_units.c.unit_kind == "chunk") == 0
    assert _run_statuses(env) == ["FAILED"]

    env.runtime.fail_chunk_outbox = False
    retried = env.chunking.create_chunks(env.ctx, source=env.source, spec=ContentChunkSpec(3, 1))
    assert retried.status == "COMMITTED"
    assert retried.is_duplicate is False


def test_weakened_source_unit_security_fails_closed_without_creating_a_run(env: _Env) -> None:
    with env.engine.begin() as transaction:
        transaction.execute(
            update(db.content_units)
            .where(and_(db.content_units.c.tenant_id == env.ctx.tenant_id, db.content_units.c.id == _BODY_ID))
            .values(security_envelope={"tenantId": "tenant-demo", "classification": "public"})
        )

    with pytest.raises(ConflictDetected, match="security envelope"):
        env.chunking.create_chunks(env.ctx, source=env.source, spec=ContentChunkSpec(4, 1))

    assert _run_statuses(env) == []
    assert (
        _row_count(env.engine, db.media_derivatives, db.media_derivatives.c.processor_name == "content_chunk_v1") == 0
    )


def _commit_source_media(
    engine: Engine,
    media_repository: SqlAlchemyMediaRepository,
    storage: LocalMediaStorageAdapter,
    runtime: _Runtime,
    ctx: RequestContext,
) -> str:
    catalog = MediaCatalogService(engine=engine, media_repository=media_repository)
    catalog.bind_collaborators({"runtime_service": runtime})
    transaction_service = MediaTransactionService(
        engine=engine,
        media_repository=media_repository,
        media_storage=storage,
    )
    transaction_service.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=media_repository, media_storage=storage)
    media_set = catalog.create_media_set(ctx, _media_set_spec())
    transaction_id = transaction_service.open(ctx, media_set_id=media_set.media_set_id, idempotency_key="source")
    session = upload.initiate(
        ctx,
        media_set_id=media_set.media_set_id,
        logical_path="/contract.pdf",
        supplied_mime_type="application/pdf",
    )
    staged = upload.complete(
        ctx,
        inputs=_upload_input(media_set.media_set_id, transaction_id),
        upload=session,
        source=io.BytesIO(b"%PDF-1.4 committed source"),
    )
    transaction_service.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _commit_layout_derivative(
    engine: Engine,
    repository: SqlAlchemyMediaDerivativeRepository,
    ctx: RequestContext,
    version_id: str,
) -> None:
    with engine.begin() as transaction:
        repository.create_derivative_or_get_existing(
            transaction=transaction,
            record=_source_derivative(ctx, version_id),
        )
        repository.insert_content_units(
            transaction=transaction,
            records=_source_units(ctx, version_id),
        )
        repository.commit_derivative(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            media_derivative_id=_SOURCE_DERIVATIVE_ID,
            committed_at="2026-07-17T00:00:00+09:00",
        )


def _commit_alternate_derivative(env: _Env) -> None:
    source_derivative = _source_derivative(env.ctx, env.version_id)
    source_unit = _source_units(env.ctx, env.version_id)[0]
    derivative = replace(
        source_derivative,
        media_derivative_id=_ALTERNATE_DERIVATIVE_ID,
        derivative_kind="pdf_ocr",
        processor_spec_hash="ocr-spec-v1",
        processor_name="pdf_ocr_v1",
        params_hash="ocr-params-v1",
        content_hash="ocr-content-v1",
    )
    unit = replace(
        source_unit,
        content_unit_id="cu-ocr-page",
        derivative_id=_ALTERNATE_DERIVATIVE_ID,
        unit_kind="ocr_page",
        chunk_spec_hash="ocr-spec-v1",
        structure={"kind": "page", "label": "ocr"},
    )
    with env.engine.begin() as transaction:
        env.derivative_repository.create_derivative_or_get_existing(transaction=transaction, record=derivative)
        env.derivative_repository.insert_content_units(transaction=transaction, records=[unit])
        env.derivative_repository.commit_derivative(
            transaction=transaction,
            tenant_id=env.ctx.tenant_id,
            media_derivative_id=_ALTERNATE_DERIVATIVE_ID,
            committed_at="2026-07-17T00:01:00+09:00",
        )


def _source_derivative(ctx: RequestContext, version_id: str) -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id=_SOURCE_DERIVATIVE_ID,
        tenant_id=ctx.tenant_id,
        source_media_item_version_id=version_id,
        derivative_kind="pdf_layout",
        processor_spec_hash="layout-spec-v1",
        processor_name="pdf_layout_v1",
        processor_version="1.0.0",
        model_name="layout-parser",
        model_version="1.0.0",
        params_hash="layout-params-v1",
        security_envelope=dict(_SECURITY),
        status="STAGED",
        content_hash="layout-content-v1",
        mime_type="application/json",
        created_at="2026-07-17T00:00:00+09:00",
    )


def _source_units(ctx: RequestContext, version_id: str) -> list[ContentUnitRecord]:
    common = {
        "tenant_id": ctx.tenant_id,
        "source_media_item_version_id": version_id,
        "derivative_id": _SOURCE_DERIVATIVE_ID,
        "unit_kind": "layout_block",
        "chunk_spec_hash": "layout-spec-v1",
        "security_envelope": dict(_SECURITY),
        "page_number": 1,
        "confidence": 0.99,
        "language": "en",
        "created_at": "2026-07-17T00:00:00+09:00",
    }
    return [
        ContentUnitRecord(
            content_unit_id=_HEADING_ID,
            ordinal=0,
            text="alpha beta gamma delta epsilon zeta",
            text_hash="heading-text-v1",
            bbox=_heading_bbox(),
            source_locator=_heading_locator(),
            structure={"kind": "heading", "level": 1, "label": "H1"},
            **common,
        ),
        ContentUnitRecord(
            content_unit_id=_BODY_ID,
            ordinal=1,
            text="eta theta iota",
            text_hash="body-text-v1",
            bbox=_body_bbox(),
            source_locator=_body_locator(),
            structure={"kind": "paragraph", "label": "body"},
            **common,
        ),
    ]


def _read_output(env: _Env, derivative_id: str) -> tuple[MediaDerivativeRecord, list[ContentUnitRecord]]:
    with env.engine.begin() as transaction:
        derivative = env.derivative_repository.derivative_by_id(
            transaction=transaction,
            tenant_id=env.ctx.tenant_id,
            media_derivative_id=derivative_id,
        )
        units = env.derivative_repository.get_content_units(
            transaction=transaction,
            tenant_id=env.ctx.tenant_id,
            derivative_id=derivative_id,
        )
    assert derivative is not None and derivative.status == "COMMITTED"
    return derivative, units


def _assert_commit_evidence_has_no_raw_text(env: _Env, derivative_id: str) -> None:
    events = [
        payload
        for event_type, payload in env.runtime.outbox
        if event_type == "media.derivative.committed" and payload.get("mediaDerivativeId") == derivative_id
    ]
    assert len(events) == 1
    serialized = str(events[0])
    assert "alpha" not in serialized and "eta" not in serialized
    assert events[0]["sourceMediaDerivativeId"] == _SOURCE_DERIVATIVE_ID


def _run_statuses(env: _Env) -> list[str]:
    with env.engine.begin() as transaction:
        rows = (
            transaction.execute(
                select(db.media_processing_runs.c.status)
                .where(db.media_processing_runs.c.processor_name == "content_chunk_v1")
                .order_by(db.media_processing_runs.c.created_at, db.media_processing_runs.c.id)
            )
            .scalars()
            .all()
        )
    return [str(status) for status in rows]


def _row_count(engine: Engine, table: object, condition: object) -> int:
    with engine.begin() as transaction:
        value = transaction.execute(select(func.count()).select_from(table).where(condition)).scalar_one()
    return int(value)


def _media_set_spec() -> MediaSetSpec:
    return MediaSetSpec(
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )


def _upload_input(media_set_id: str, transaction_id: str) -> MediaUploadInput:
    return MediaUploadInput(
        media_set_id=media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/contract.pdf",
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope=dict(_SECURITY),
    )


def _heading_bbox() -> dict[str, object]:
    return {"x": 20, "y": 40, "width": 400, "height": 40, "pageWidth": 612, "pageHeight": 792, "unit": "pt"}


def _body_bbox() -> dict[str, object]:
    return {"x": 20, "y": 90, "width": 500, "height": 120, "pageWidth": 612, "pageHeight": 792, "unit": "pt"}


def _heading_locator() -> dict[str, object]:
    return {"pageNumber": 1, "bbox": _heading_bbox(), "coordinateSystem": "pdf_points"}


def _body_locator() -> dict[str, object]:
    return {"pageNumber": 1, "bbox": _body_bbox(), "coordinateSystem": "pdf_points"}
