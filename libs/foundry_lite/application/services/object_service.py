from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.indexing import ObjectIndexingService
from foundry_lite.application.services.object_store.links import ObjectLinksService
from foundry_lite.application.services.object_store.query import ObjectQueryService
from foundry_lite.application.services.object_store.records import ObjectRecordsService
from foundry_lite.application.services.object_store.sets import ObjectSetsService


@dataclass(frozen=True)
class ObjectServices:
    """Object-store application service group without multiple-inheritance composition."""

    indexing: ObjectIndexingService
    links: ObjectLinksService
    query: ObjectQueryService
    records: ObjectRecordsService
    sets: ObjectSetsService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> ObjectServices:
        return cls(
            indexing=ObjectIndexingService(dependencies),
            links=ObjectLinksService(dependencies),
            query=ObjectQueryService(dependencies),
            records=ObjectRecordsService(dependencies),
            sets=ObjectSetsService(dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.indexing, self.links, self.query, self.records, self.sets)
