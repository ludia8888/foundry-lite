"""Application service helpers for object service workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.object_store.indexing import ObjectIndexingService
from foundry_lite.application.services.object_store.indexing_services import ObjectIndexingServices
from foundry_lite.application.services.object_store.interface_query import ObjectInterfaceQueryService
from foundry_lite.application.services.object_store.links import ObjectLinksService
from foundry_lite.application.services.object_store.query import ObjectQueryService
from foundry_lite.application.services.object_store.records import ObjectRecordsService
from foundry_lite.application.services.object_store.search import ObjectSearchService
from foundry_lite.application.services.object_store.sets import ObjectSetsService
from foundry_lite.application.services.object_store.subscriptions import ObjectSubscriptionService


@dataclass(frozen=True)
class ObjectServices:
    """Object-store application service group without multiple-inheritance composition."""

    indexing: ObjectIndexingService
    indexing_services: ObjectIndexingServices
    interface_query: ObjectInterfaceQueryService
    links: ObjectLinksService
    query: ObjectQueryService
    records: ObjectRecordsService
    search: ObjectSearchService
    sets: ObjectSetsService
    subscriptions: ObjectSubscriptionService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> ObjectServices:
        indexing_services = ObjectIndexingServices.create(dependencies)
        return cls(
            indexing=indexing_services.entrypoint,
            indexing_services=indexing_services,
            interface_query=build_service(ObjectInterfaceQueryService, dependencies),
            links=build_service(ObjectLinksService, dependencies),
            query=build_service(ObjectQueryService, dependencies),
            records=build_service(ObjectRecordsService, dependencies),
            search=build_service(ObjectSearchService, dependencies),
            sets=build_service(ObjectSetsService, dependencies),
            subscriptions=build_service(ObjectSubscriptionService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            *self.indexing_services.items(),
            self.interface_query,
            self.links,
            self.query,
            self.records,
            self.search,
            self.sets,
            self.subscriptions,
        )
