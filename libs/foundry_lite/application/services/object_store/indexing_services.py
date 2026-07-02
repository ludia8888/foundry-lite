"""Service group for the ObjectIndexing bounded context."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.object_store.indexing import ObjectIndexingService
from foundry_lite.application.services.object_store.indexing_cdc_service import ObjectCdcIndexingService
from foundry_lite.application.services.object_store.indexing_link_service import ObjectLinkIndexingService
from foundry_lite.application.services.object_store.indexing_ontology_reindex_service import (
    ObjectOntologyReindexService,
)
from foundry_lite.application.services.object_store.indexing_rebuild_service import ObjectIndexRebuildService
from foundry_lite.application.services.object_store.indexing_record_mutations import ObjectIndexRecordMutationService
from foundry_lite.application.services.object_store.indexing_shadow_service import ObjectIndexShadowService


@dataclass(frozen=True)
class ObjectIndexingServices:
    """Object indexing use-case services plus the stable compatibility entrypoint."""

    cdc: ObjectCdcIndexingService
    entrypoint: ObjectIndexingService
    links: ObjectLinkIndexingService
    ontology_reindex: ObjectOntologyReindexService
    record_mutations: ObjectIndexRecordMutationService
    rebuild: ObjectIndexRebuildService
    shadow: ObjectIndexShadowService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> ObjectIndexingServices:
        cdc = build_service(ObjectCdcIndexingService, dependencies)
        links = build_service(ObjectLinkIndexingService, dependencies)
        record_mutations = build_service(ObjectIndexRecordMutationService, dependencies)
        rebuild = build_service(ObjectIndexRebuildService, dependencies)
        shadow = build_service(ObjectIndexShadowService, dependencies)
        ontology_reindex = build_service(ObjectOntologyReindexService, dependencies)
        return cls(
            cdc=cdc,
            entrypoint=ObjectIndexingService(
                cdc_indexing_service=cdc,
                index_rebuild_service=rebuild,
                index_record_mutation_service=record_mutations,
                index_shadow_service=shadow,
                ontology_reindex_service=ontology_reindex,
            ),
            links=links,
            ontology_reindex=ontology_reindex,
            record_mutations=record_mutations,
            rebuild=rebuild,
            shadow=shadow,
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.cdc, self.links, self.ontology_reindex, self.record_mutations, self.rebuild, self.shadow)
