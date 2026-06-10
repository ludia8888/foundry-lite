from __future__ import annotations

from typing import Any, Literal, overload

from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class DatasetVersionMixin(CoreServiceMixin):
    def _next_dataset_version_number(self, conn: Any, dataset_id: str) -> int:
        return self.dataset_version_repository.next_version_number(transaction=conn, dataset_id=dataset_id)

    def _schema_for_version(self, dataset_id: str, schema_version: int) -> dict[str, Any]:
        row = self.dataset_version_repository.schema_for_version(
            dataset_id=dataset_id,
            schema_version=schema_version,
        )
        if row is None:
            raise NotFound("dataset schema not found")
        return row

    def _get_version(
        self,
        dataset_id: str,
        version: str,
        *,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        with self.engine.begin() as conn:
            if version == "latest":
                return self._latest_version_by_dataset_id(conn, dataset_id)
            row = self.dataset_version_repository.version_by_id(transaction=conn, version_id=version)
            if row is None:
                raise NotFound("dataset version not found", details={"version": version})
            if row["tenant_id"] != ctx.tenant_id:
                raise NotFound("dataset version not found", details={"version": version})
            return row

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: Any,
        dataset_id: str,
        *,
        allow_missing: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: Any,
        dataset_id: str,
        *,
        allow_missing: Literal[True],
    ) -> dict[str, Any] | None: ...

    def _latest_version_by_dataset_id(
        self,
        conn: Any,
        dataset_id: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        row = self.dataset_version_repository.latest_version_by_dataset_id(
            transaction=conn,
            dataset_id=dataset_id,
        )
        if row is None:
            if allow_missing:
                return None
            raise NotFound("dataset has no committed version", details={"dataset_id": dataset_id})
        return row

    def _get_version_by_id(self, version_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            version = self.dataset_version_repository.version_by_id(transaction=conn, version_id=version_id)
            if version is None:
                raise NotFound("dataset version not found", details={"version_id": version_id})
            return version
