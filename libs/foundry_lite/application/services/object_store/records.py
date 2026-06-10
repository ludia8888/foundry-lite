from __future__ import annotations

from typing import Any

from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class ObjectRecordsService(CoreService):
    required_dependencies = ("object_read_repository",)
    required_collaborators = ()

    def _object_record(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        return self.object_read_repository.object_record(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type_api_name,
            object_id=object_id,
        )
