"""Deterministic fake ``ToolExecutor`` for AIP ToolBroker tests and local runtime."""

from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.tool_executor import ToolExecutionRequest, ToolExecutionResult
from foundry_lite.domain.context import RequestContext


class FakeToolExecutor:
    """Pure-python read-only executor with no network, SQL, shell, or provider calls."""

    profile_name = "fake-tool-executor"

    def execute(self, ctx: RequestContext, request: ToolExecutionRequest) -> ToolExecutionResult:
        output = {
            "tenant_id": ctx.tenant_id,
            "actor_user_id": ctx.actor_user_id,
            "tool_id": request.tool_id,
            "version": request.version,
            "input": dict(request.input_json),
        }
        return ToolExecutionResult(output_json=output, result_ref=f"fake-tool://{request.tool_id}")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "execute",
                    "validation",
                    False,
                    "The fake tool executor received malformed broker-validated input.",
                ),
            ),
        )
