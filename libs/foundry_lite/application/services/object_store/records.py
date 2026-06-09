from __future__ import annotations

from typing import Any

from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from sqlalchemy import and_, select
from sqlalchemy.engine import Connection


class ObjectRecordsMixin(CoreServiceMixin):
    def _object_record(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        row = (
            conn.execute(
                select(db.object_records).where(
                    and_(
                        db.object_records.c.tenant_id == ctx.tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id == object_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
