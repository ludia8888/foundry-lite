from __future__ import annotations

from typing import Protocol

from foundry_lite.application.ports import LineageEdgeRow, ObjectRecordRow, RuntimeRunLink, TransactionContext
from foundry_lite.domain.context import RequestContext


class ObjectRecordLookup(Protocol):
    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
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
