"""Application service helpers for query protocols workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foundry_lite.application.ports import (
    LineageEdgeRow,
    ObjectQueryResult,
    ObjectRecordRow,
    ObjectTypeRow,
    PropertyTypeRow,
    RuntimeJsonObject,
    RuntimeRunLink,
    TransactionContext,
)
from foundry_lite.domain.context import RequestContext


class ObjectRecordLookup(Protocol):
    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> ObjectRecordRow | None: ...


class ObjectLineageReader(Protocol):
    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[LineageEdgeRow]: ...

    def source_run_chain(
        self,
        source_dataset_version_id: str,
        *,
        object_type_api_name: str,
        ctx: RequestContext | None = None,
    ) -> list[RuntimeRunLink]: ...

    def late_data_badge_for_source(
        self,
        source_dataset_version_id: str,
        *,
        object_type_api_name: str,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject | None: ...


class ObjectQueryOntologyLookup(Protocol):
    def _active_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ObjectTypeRow: ...

    def _properties_for_object_type(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> Sequence[PropertyTypeRow]: ...


class ObjectSearchQueryPlanner(Protocol):
    def search_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext,
        search_text: str,
        limit: int,
    ) -> ObjectQueryResult: ...

    def search_objects_semantic(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext,
        semantic_text: str,
        limit: int,
    ) -> ObjectQueryResult: ...
