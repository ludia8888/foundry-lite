"""Application service helpers for indexing workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexSchema,
    IndexedContentUnit,
)
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, NotFound

_PROJECTION_VERSION = 1


@dataclass(frozen=True)
class IndexingOutcome:
    """Result of projecting content units into one index generation."""

    generation: str
    indexed: int
    failed: int
    # The generation search reads right now. Indexing writes into a shadow, so a caller that
    # wants its upload to become searchable promotes with this as the compare-and-swap base.
    active_generation: str = ""


class MediaIndexingService(CoreService):
    """Project committed content units into a rebuildable content-search index (doc §10).

    Content units in the DB are the truth; this service reads them and upserts a projection
    into a named index generation (shadow-then-switch). A rebuild reconstructs the projection
    purely from committed DB rows, proving the index is disposable.
    """

    required_dependencies = (
        "engine",
        "media_repository",
        "media_derivative_repository",
        "content_index_adapter",
        "embedding_model_adapter",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def configure(self, ctx: RequestContext, *, generation: str) -> None:
        self.content_index_adapter.configure_generation(ContentIndexSchema(generation=generation))

    def index_derivative(self, ctx: RequestContext, *, media_derivative_id: str, generation: str) -> IndexingOutcome:
        with self.engine.begin() as conn:
            derivative = self.media_derivative_repository.derivative_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_derivative_id=media_derivative_id
            )
            if derivative is None:
                raise NotFound("media derivative not found", details={"media_derivative_id": media_derivative_id})
            if derivative.status != "COMMITTED":
                raise ConflictDetected(
                    "only a COMMITTED derivative can be indexed",
                    details={"media_derivative_id": media_derivative_id, "status": derivative.status},
                )
            units = self.media_derivative_repository.get_content_units(
                transaction=conn, tenant_id=ctx.tenant_id, derivative_id=media_derivative_id
            )
            media_set_ids = self._media_set_ids(conn, ctx, {unit.source_media_item_version_id for unit in units})
        outcome = self._project(generation, units, media_set_ids)
        self._audit_indexed(ctx, generation, media_derivative_id, outcome)
        return outcome

    def rebuild(
        self, ctx: RequestContext, *, generation: str, source_media_item_version_ids: list[str]
    ) -> IndexingOutcome:
        with self.engine.begin() as conn:
            units = self.media_derivative_repository.get_committed_content_units_for_versions(
                transaction=conn, tenant_id=ctx.tenant_id, source_media_item_version_ids=source_media_item_version_ids
            )
            media_set_ids = self._media_set_ids(conn, ctx, {unit.source_media_item_version_id for unit in units})
        outcome = self._project(generation, units, media_set_ids)
        self._audit_indexed(ctx, generation, "rebuild", outcome)
        return outcome

    def promote(self, ctx: RequestContext, *, expected_active: str, generation: str) -> None:
        self.content_index_adapter.promote_generation(expected_active, generation)
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.content_index.promoted",
                resource_type="content_index_generation",
                resource_id=generation,
                action="update",
                after_ref={"expectedActive": expected_active, "generation": generation},
                correlation_id=ctx.request_id,
            )

    def _media_set_ids(self, conn: TransactionContext, ctx: RequestContext, version_ids: set[str]) -> dict[str, str]:
        """Resolve each source version to its owning media set.

        Scoped search filters on the projection, so the owning media set has to be written into
        the projection at index time. Resolving per source version (not per unit) keeps this to
        two lookups per distinct version even when a derivative produced hundreds of chunks.
        """
        resolved: dict[str, str] = {}
        for version_id in sorted(version_ids):
            version = self.media_repository.media_item_version_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=version_id
            )
            if version is None:
                continue
            item = self.media_repository.media_item_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_id=version.media_item_id
            )
            if item is not None:
                resolved[version_id] = item.media_set_id
        return resolved

    def _project(
        self, generation: str, units: list[ContentUnitRecord], media_set_ids: dict[str, str]
    ) -> IndexingOutcome:
        model_version = self.embedding_model_adapter.model_version if self.embedding_model_adapter.is_available else ""
        self.content_index_adapter.configure_generation(
            ContentIndexSchema(generation=generation, embedding_model_version=model_version)
        )
        batch = ContentIndexBatch(generation=generation, units=self._indexed_units(units, model_version, media_set_ids))
        result = self.content_index_adapter.upsert_units(batch)
        return IndexingOutcome(
            generation=generation,
            indexed=result.indexed,
            failed=result.failed,
            active_generation=self.content_index_adapter.active_generation(),
        )

    def _indexed_units(
        self, units: list[ContentUnitRecord], model_version: str, media_set_ids: dict[str, str]
    ) -> tuple[IndexedContentUnit, ...]:
        if not self.embedding_model_adapter.is_available:
            return tuple(_indexed(unit, (), "", media_set_ids) for unit in units)
        vectors = self.embedding_model_adapter.embed([unit.text for unit in units])
        if len(vectors) != len(units):
            # The port contract is one vector per text; a short/long batch must surface as a
            # failure, never silently drop trailing units (which would leave them unindexed and
            # unreported, violating the partial-failure contract).
            raise InvariantViolation(
                "embedding model returned a vector count that does not match the content units",
                details={"units": len(units), "vectors": len(vectors)},
            )
        return tuple(
            _indexed(unit, tuple(vector), model_version, media_set_ids)
            for unit, vector in zip(units, vectors, strict=True)
        )

    def _audit_indexed(self, ctx: RequestContext, generation: str, resource_id: str, outcome: IndexingOutcome) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._outbox(
                conn,
                ctx,
                "media.content_index.upserted",
                "content_index_generation",
                generation,
                {"generation": generation, "indexed": outcome.indexed, "failed": outcome.failed},
                idempotency_key=f"{generation}:{resource_id}",
                correlation_id=ctx.request_id,
            )
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.content_index.upserted",
                resource_type="content_index_generation",
                resource_id=resource_id,
                action="update",
                after_ref={"generation": generation, "indexed": outcome.indexed, "failed": outcome.failed},
                correlation_id=ctx.request_id,
            )


def _indexed(
    unit: ContentUnitRecord,
    embedding: EmbeddingVector,
    embedding_model_version: str,
    media_set_ids: dict[str, str],
) -> IndexedContentUnit:
    return IndexedContentUnit(
        tenant_id=unit.tenant_id,
        content_unit_id=unit.content_unit_id,
        source_media_item_version_id=unit.source_media_item_version_id,
        text=unit.text,
        text_hash=unit.text_hash,
        version=_PROJECTION_VERSION,
        page_number=unit.page_number,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        chunk_spec_hash=unit.chunk_spec_hash,
        embedding=embedding,
        embedding_model_version=embedding_model_version,
        # AIP P0a: pin the unit's classification onto the projection from its source content_unit's
        # security_envelope. The index is derived from truth; it carries the mandatory control
        # property so retrieval can PRE-filter by classification before ranking.
        classification=str(unit.security_envelope.get("classification", "")),
        media_set_id=media_set_ids.get(unit.source_media_item_version_id, ""),
    )
