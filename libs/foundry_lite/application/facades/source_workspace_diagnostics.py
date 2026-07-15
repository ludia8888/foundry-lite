"""Source workspace facade entrypoints for live connection diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry_lite.domain.context import RequestContext

if TYPE_CHECKING:
    from foundry_lite.application.services.source_connection_test_service import SourceConnectionTestService
    from foundry_lite.application.services.source_lifecycle_service import SourceLifecycleService


class SourceWorkspaceDiagnostics:
    """Keep Source diagnostic facade behavior separate from onboarding workflows."""

    def __init__(
        self,
        connection_test: SourceConnectionTestService,
        lifecycle: SourceLifecycleService,
    ) -> None:
        self._connection_test = connection_test
        self._lifecycle_diagnostics = lifecycle

    def get_managed_streaming_sync_status(
        self, sync_name: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._lifecycle_diagnostics.get_managed_streaming_sync_status(sync_name, ctx=ctx)

    def test_connection(
        self,
        source_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._connection_test.test_source_connection(
            source_name,
            expected_config_fingerprint=expected_config_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_connection_tests(
        self,
        source_name: str,
        *,
        limit: int = 20,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, object]]:
        return self._connection_test.list_source_connection_tests(source_name, limit=limit, ctx=ctx)

    def list_egress_attempts(
        self,
        source_name: str,
        *,
        limit: int = 20,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, object]]:
        return self._connection_test.list_source_egress_attempts(source_name, limit=limit, ctx=ctx)
