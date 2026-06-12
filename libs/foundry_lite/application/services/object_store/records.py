from __future__ import annotations

from foundry_lite.application.ports import ObjectRecordRow, TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class ObjectRecordsService(CoreService):
    required_dependencies = ("object_read_repository",)
    required_collaborators = ()

    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> ObjectRecordRow | None:
        return self.object_read_repository.object_record(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type_api_name,
            object_id=object_id,
        )
