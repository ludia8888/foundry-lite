"""Application service helpers for osdk application idempotency workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import (
    OsdkApplicationRepository,
    OsdkDeveloperConsoleIdempotencyRecord,
    RuntimeJsonObject,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.osdk_application_artifacts import _request_hash, _with_download_token
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class OsdkApplicationIdempotencyMixin:
    osdk_application_repository: OsdkApplicationRepository
    root: Path

    def _idempotent_response(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        operation: str,
        idempotency_key: str,
        request: Mapping[str, object],
        create_response: Callable[[], RuntimeJsonObject],
    ) -> RuntimeJsonObject:
        request_hash = _request_hash(request)
        existing = self._idempotency_record(conn, ctx, operation, idempotency_key, request_hash)
        if existing is not None:
            return existing
        response = create_response()
        stored = self._store_idempotency_record(conn, ctx, operation, idempotency_key, request_hash, response)
        return stored

    def _idempotent_download_response(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        idempotency_key: str,
        request: Mapping[str, object],
        create_response: Callable[[str], RuntimeJsonObject],
    ) -> RuntimeJsonObject:
        request_hash = _request_hash(request)
        existing = self._idempotency_record(
            conn, ctx, "osdk.release_artifact.download_token.create", idempotency_key, request_hash
        )
        if existing is not None:
            return _with_download_token(self.root, ctx, existing, request_hash)
        response = create_response(request_hash)
        record_response = {key: value for key, value in response.items() if key != "downloadToken"}
        stored = self._store_idempotency_record(
            conn,
            ctx,
            "osdk.release_artifact.download_token.create",
            idempotency_key,
            request_hash,
            cast(RuntimeJsonObject, record_response),
        )
        return _with_download_token(self.root, ctx, stored, request_hash)

    def _idempotency_record(
        self, conn: TransactionContext, ctx: RequestContext, operation: str, idempotency_key: str, request_hash: str
    ) -> RuntimeJsonObject | None:
        row = self.osdk_application_repository.developer_console_idempotency_record(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ConflictDetected("OSDK Developer Console idempotency key reused with a different request")
        return row["response_json"]

    def _store_idempotency_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        response: RuntimeJsonObject,
    ) -> RuntimeJsonObject:
        row = self.osdk_application_repository.insert_developer_console_idempotency_record(
            transaction=conn,
            record=OsdkDeveloperConsoleIdempotencyRecord(
                record_id=_new_id("osdk_idem"),
                tenant_id=ctx.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_json=response,
                status="succeeded",
                created_at=_now(),
            ),
        )
        if row["request_hash"] != request_hash:
            raise ConflictDetected("OSDK Developer Console idempotency key changed concurrently")
        return row["response_json"]
