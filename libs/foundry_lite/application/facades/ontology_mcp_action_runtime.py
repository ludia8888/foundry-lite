"""Consumer MCP adapter over public Action reads and narrow machine entrypoints."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import ActionCatalogItem, ActionExecutionPlanResponse
from foundry_lite.application.facades.action_gateway import ActionGateway
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.domain.context import RequestContext


class OntologyMcpActionRuntimeAdapter:
    """Keep machine-only Action methods out of the general REST/SDK facade."""

    def __init__(self, actions: ActionGateway, action_service: ActionService) -> None:
        self._actions = actions
        self._action_service = action_service

    def get(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem:
        return self._actions.get(action_api_name, ctx=ctx)

    def get_external_mcp(self, action_api_name: str, *, ctx: RequestContext) -> ActionCatalogItem:
        return self._action_service.get_external_mcp_action(action_api_name, ctx=ctx)

    def plan(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse:
        return self._actions.plan(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            branch_id=branch_id,
            ctx=ctx,
        )

    def plan_external_mcp(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext,
    ) -> ActionExecutionPlanResponse:
        return self._action_service.plan_external_mcp_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
        )

    def start_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._actions.start_run(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx,
        )

    def start_external_mcp_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return self._action_service.start_external_mcp_action_run(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx,
        )

    def resume_idempotent_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object] | None:
        return self._actions.resume_idempotent_run(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def resume_external_mcp_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext,
    ) -> dict[str, object] | None:
        return self._action_service.resume_external_mcp_action_run(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._actions.get_run(run_id, ctx=ctx)

    def get_external_mcp_run(self, run_id: str, *, ctx: RequestContext) -> dict[str, object]:
        return self._action_service.get_external_mcp_action_run(run_id, ctx=ctx)
