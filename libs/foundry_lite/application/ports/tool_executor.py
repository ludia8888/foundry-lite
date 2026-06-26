"""Tool execution port (AIP-lite P0e — ToolBroker executor boundary, §8.8).

The model never executes tools directly. It can only request a tool call; the
ToolBroker validates that request and then delegates the final read-only
execution to this server-side port under the invoking user's ``RequestContext``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.domain.context import RequestContext

ToolEffect = Literal["READ", "PROPOSE_WRITE", "WRITE"]
ConfirmationPolicy = Literal["NONE", "USER", "HUMAN_REVIEW"]
ToolExecutionStatus = Literal["succeeded", "failed"]
ToolJsonObject = Mapping[str, object]


@dataclass(frozen=True)
class ToolExecutionRequest:
    """Validated tool call handed to an infrastructure/domain executor."""

    tool_id: str
    version: str
    input_json: ToolJsonObject
    ai_run_id: str
    request_id: str
    idempotency_key: str


@dataclass(frozen=True)
class ToolExecutionResult:
    """Server-side tool result before broker output masking/size limits."""

    output_json: ToolJsonObject
    status: ToolExecutionStatus = "succeeded"
    result_ref: str = ""
    error_json: ToolJsonObject | None = None


@runtime_checkable
class ToolExecutor(Protocol):
    """Server-side executor called only after ToolBroker validation."""

    @property
    def profile_name(self) -> str:
        """Human-readable executor profile for diagnostics."""
        ...

    def execute(self, ctx: RequestContext, request: ToolExecutionRequest) -> ToolExecutionResult:
        """Execute one validated tool as the invoking user/tenant."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare failure semantics for CI and operations evidence."""
        ...
