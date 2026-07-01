"""Application service helpers for versions workflows."""

from __future__ import annotations

from typing import Literal, overload

from foundry_lite.application.ports.dataset_quality_repository import DatasetSchemaRow
from foundry_lite.application.ports.dataset_version_repository import DatasetVersionRow
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class DatasetVersionService(CoreService):
    required_dependencies = ("engine", "dataset_version_repository")
    required_collaborators = ()

    def _next_dataset_version_number(self, conn: TransactionContext, dataset_id: str) -> int:
        return self.dataset_version_repository.next_version_number(transaction=conn, dataset_id=dataset_id)

    def _schema_for_version(self, dataset_id: str, schema_version: int) -> DatasetSchemaRow:
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
    ) -> DatasetVersionRow:
        with self.engine.begin() as conn:
            if version == "latest":
                return self._latest_version_by_dataset_id(conn, dataset_id)
            row = self.dataset_version_repository.version_by_dataset_id_and_id(
                transaction=conn,
                dataset_id=dataset_id,
                version_id=version,
            )
            if row is None:
                raise NotFound("dataset version not found", details={"version": version})
            if row["tenant_id"] != ctx.tenant_id:
                raise NotFound("dataset version not found", details={"version": version})
            return row

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: Literal[False] = False,
    ) -> DatasetVersionRow: ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: Literal[True],
    ) -> DatasetVersionRow | None: ...

    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: bool = False,
    ) -> DatasetVersionRow | None:
        row = self.dataset_version_repository.latest_version_by_dataset_id(
            transaction=conn,
            dataset_id=dataset_id,
        )
        if row is None:
            if allow_missing:
                return None
            raise NotFound("dataset has no committed version", details={"dataset_id": dataset_id})
        return row

    def _version_by_id(self, conn: TransactionContext, version_id: str) -> DatasetVersionRow:
        version = self.dataset_version_repository.version_by_id(transaction=conn, version_id=version_id)
        if version is None:
            raise NotFound("dataset version not found", details={"version_id": version_id})
        return version

    def _get_version_by_id(self, version_id: str) -> DatasetVersionRow:
        with self.engine.begin() as conn:
            return self._version_by_id(conn, version_id)
