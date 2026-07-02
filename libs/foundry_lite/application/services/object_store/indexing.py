"""Compatibility entrypoint for the ObjectIndexing bounded context."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    ObjectIndexCdcResult,
    ObjectIndexRebuildResult,
    ObjectIndexShadowRebuildResult,
    ObjectIndexValidationResult,
    ObjectPropertyMap,
    OntologyObjectReindexResult,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.services.object_store.indexing_cdc_service import ObjectCdcIndexingService
from foundry_lite.application.services.object_store.indexing_ontology_reindex_service import (
    ObjectOntologyReindexService,
)
from foundry_lite.application.services.object_store.indexing_rebuild_service import ObjectIndexRebuildService
from foundry_lite.application.services.object_store.indexing_record_mutations import ObjectIndexRecordMutationService
from foundry_lite.application.services.object_store.indexing_shadow_service import ObjectIndexShadowService
from foundry_lite.application.services.object_store.indexing_types import ObjectIndexRebuildPlan
from foundry_lite.domain.context import RequestContext


class ObjectIndexingService:
    """Stable public service surface backed by explicit indexing use cases."""

    required_dependencies = ()
    required_collaborators = ()
    _cdc_indexing_service: ObjectCdcIndexingService
    _index_rebuild_service: ObjectIndexRebuildService
    _index_record_mutation_service: ObjectIndexRecordMutationService
    _index_shadow_service: ObjectIndexShadowService
    _ontology_reindex_service: ObjectOntologyReindexService

    def __init__(
        self,
        *,
        cdc_indexing_service: ObjectCdcIndexingService,
        index_rebuild_service: ObjectIndexRebuildService,
        index_record_mutation_service: ObjectIndexRecordMutationService,
        index_shadow_service: ObjectIndexShadowService,
        ontology_reindex_service: ObjectOntologyReindexService,
    ) -> None:
        self._cdc_indexing_service = cdc_indexing_service
        self._index_rebuild_service = index_rebuild_service
        self._index_record_mutation_service = index_record_mutation_service
        self._index_shadow_service = index_shadow_service
        self._ontology_reindex_service = ontology_reindex_service

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        return self._index_rebuild_service.index_rebuild(object_type_api_name, ctx=ctx)

    def index_shadow_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        expected_count: int | None = None,
        expected_hash: str | None = None,
    ) -> ObjectIndexShadowRebuildResult:
        return self._index_rebuild_service.index_shadow_rebuild(
            object_type_api_name,
            ctx=ctx,
            expected_count=expected_count,
            expected_hash=expected_hash,
        )

    def index_replay_run(
        self,
        index_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexRebuildResult:
        return self._index_rebuild_service.index_replay_run(index_run_id, ctx=ctx)

    def index_ontology_reindex_plan(
        self,
        object_type_api_name: str,
        reindex_key: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyObjectReindexResult:
        return self._ontology_reindex_service.index_ontology_reindex_plan(
            object_type_api_name,
            reindex_key,
            ctx=ctx,
        )

    def index_cdc_events(
        self,
        object_type_api_name: str,
        events: Sequence[ObjectPropertyMap],
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexCdcResult:
        return self._cdc_indexing_service.index_cdc_events(object_type_api_name, events, ctx=ctx)

    def _merge_properties(
        self,
        conn: TransactionContext,
        object_type_id: str,
        base: ObjectPropertyMap,
        edits: ObjectPropertyMap,
    ) -> ObjectPropertyMap:
        return self._index_record_mutation_service.merge_properties(conn, object_type_id, base, edits)

    def _read_index_source_rows(self, plan: ObjectIndexRebuildPlan) -> list[TabularRow]:
        return list(self._index_rebuild_service._read_index_source_rows(plan))

    def _validate_shadow_index(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: ObjectIndexRebuildPlan,
        expected_count: int | None,
        expected_hash: str | None,
    ) -> ObjectIndexValidationResult:
        return self._index_shadow_service.validate_shadow_index(conn, ctx, plan, expected_count, expected_hash)
