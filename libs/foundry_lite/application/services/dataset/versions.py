from __future__ import annotations

from typing import Any, Literal, overload

from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound
from foundry_lite.infrastructure import schema as db
from sqlalchemy import and_, desc, func, select
from sqlalchemy.engine import Connection


class DatasetVersionMixin(CoreServiceMixin):
    def _next_dataset_version_number(self, conn: Connection, dataset_id: str) -> int:
        latest = (
            conn.execute(
                select(func.max(db.dataset_versions.c.version_number)).where(
                    db.dataset_versions.c.dataset_id == dataset_id
                )
            ).scalar()
            or 0
        )
        return int(latest) + 1

    def _schema_for_version(self, dataset_id: str, schema_version: int) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.dataset_schemas).where(
                        and_(
                            db.dataset_schemas.c.dataset_id == dataset_id,
                            db.dataset_schemas.c.version == schema_version,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("dataset schema not found")
            return dict(row)

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
            row = self._select_by_id(conn, db.dataset_versions, version)
            if row is None:
                raise NotFound("dataset version not found", details={"version": version})
            if row["tenant_id"] != ctx.tenant_id:
                raise NotFound("dataset version not found", details={"version": version})
            return row

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: Connection,
        dataset_id: str,
        *,
        allow_missing: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: Connection,
        dataset_id: str,
        *,
        allow_missing: Literal[True],
    ) -> dict[str, Any] | None: ...

    def _latest_version_by_dataset_id(
        self,
        conn: Connection,
        dataset_id: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        row = (
            conn.execute(
                select(db.dataset_versions)
                .where(db.dataset_versions.c.dataset_id == dataset_id)
                .order_by(desc(db.dataset_versions.c.version_number))
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            if allow_missing:
                return None
            raise NotFound("dataset has no committed version", details={"dataset_id": dataset_id})
        return dict(row)

    def _get_version_by_id(self, version_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            version = self._select_by_id(conn, db.dataset_versions, version_id)
            if version is None:
                raise NotFound("dataset version not found", details={"version_id": version_id})
            return version
