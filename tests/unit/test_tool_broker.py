"""Unit tests for ``ToolBrokerService`` (AIP-lite P0e, §8.8/§9.5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.tool_executor import (
    ConfirmationPolicy,
    ToolEffect,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from foundry_lite.application.services.aip.tool_broker import (
    ToolBrokerError,
    ToolBrokerRequest,
    ToolBrokerService,
    ToolCallRequest,
    ToolSpec,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.security.policy import PolicyService

_CTX = RequestContext(tenant_id="tenant-demo", actor_user_id="user-1", roles=("viewer",), request_id="req-1")


def test_tool_broker_executes_read_tool_as_invoking_user_and_returns_ledger_record() -> None:
    executor = _SpyExecutor(output={"object_type": "PurchaseOrder", "properties": {"id": "PO-1042"}})

    result = _broker(executor).execute(_CTX, _request())

    assert executor.calls == 1
    assert executor.contexts == [_CTX]
    assert result.authorization_decision == "allowed"
    assert result.arguments_hash.startswith("sha256:")
    assert result.result_hash.startswith("sha256:")
    assert result.ledger_record.tenant_id == "tenant-demo"
    assert result.ledger_record.tool_id == "ontology.get_object"
    assert result.ledger_record.effect == "READ"
    assert result.output_json["properties"] == {"id": "PO-1042"}


def test_tool_not_in_agent_manifest_rejected() -> None:
    executor = _SpyExecutor()

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(agent_allowed_tools=()))

    assert excinfo.value.reason == "not_allowed"
    assert executor.calls == 0


def test_generic_executor_tool_is_denied_before_executor_call() -> None:
    executor = _SpyExecutor()

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(tool_id="shell_execute", agent_allowed_tools=("shell_execute",)))

    assert excinfo.value.reason == "not_published"
    assert executor.calls == 0


def test_write_tool_requires_confirmation_or_review() -> None:
    executor = _SpyExecutor()
    spec = _spec(tool_id="action.propose", effect="WRITE", confirmation_policy="HUMAN_REVIEW")

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(spec=spec, tool_id="action.propose"))

    assert excinfo.value.reason == "confirmation_required"
    assert executor.calls == 0


def test_tool_broker_rejects_input_schema_mismatch_before_executor_call() -> None:
    executor = _SpyExecutor()

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(arguments_json='{"object_type":"PurchaseOrder"}'))

    assert excinfo.value.reason == "schema_invalid"
    assert executor.calls == 0


def test_tool_broker_rejects_masked_property_before_executor_call() -> None:
    executor = _SpyExecutor()

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(arguments_json=_args(properties=("margin",))))

    assert excinfo.value.reason == "masked_property_denied"
    assert executor.calls == 0


def test_tool_broker_rejects_non_exportable_tool_result_before_executor_call() -> None:
    executor = _SpyExecutor()
    spec = _spec(result_classification="restricted")

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(spec=spec))

    assert excinfo.value.reason == "egress_incompatible"
    assert executor.calls == 0


def test_tool_broker_masks_output_before_returning_to_model() -> None:
    executor = _SpyExecutor(output={"object_type": "PurchaseOrder", "properties": {"id": "PO-1042", "margin": 1200}})

    result = _broker(executor).execute(_CTX, _request())

    assert result.output_json["properties"] == {"id": "PO-1042", "margin": "***MASKED***"}
    assert '"margin":"***MASKED***"' in result.redacted_preview


def test_tool_broker_enforces_result_item_limit() -> None:
    executor = _SpyExecutor(output={"items": [{"id": "1"}, {"id": "2"}]})
    spec = _spec(max_result_items=1)

    with pytest.raises(ToolBrokerError) as excinfo:
        _broker(executor).execute(_CTX, _request(spec=spec))

    assert excinfo.value.reason == "budget_exceeded"
    assert executor.calls == 1


def test_local_runtime_composes_tool_broker_with_safe_fake_executor(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "tool-broker-runtime")
    dependencies.metadata_repository.initialize_schema()
    services = CoreServices.create(dependencies)

    result = services.tool_broker.execute(_CTX, _request())

    assert result.output_json["tenant_id"] == "tenant-demo"
    assert result.output_json["actor_user_id"] == "user-1"
    assert result.output_json["tool_id"] == "ontology.get_object"
    assert result.ledger_record.arguments_hash.startswith("sha256:")


def _broker(executor: _SpyExecutor) -> ToolBrokerService:
    return ToolBrokerService(policy=PolicyService(_classification_provider), tool_executor=executor)


def _request(
    *,
    spec: ToolSpec | None = None,
    tool_id: str = "ontology.get_object",
    arguments_json: str | None = None,
    agent_allowed_tools: tuple[str, ...] | None = None,
) -> ToolBrokerRequest:
    return ToolBrokerRequest(
        tool_call=ToolCallRequest(
            tool_call_id="tool-call-1",
            tool_id=tool_id,
            version="2026-06-25",
            arguments_json=arguments_json or _args(),
            ai_run_id="ai-run-1",
            sequence=1,
            request_id="req-1",
            occurred_at="2026-06-25T00:00:00Z",
        ),
        tool_manifest=(spec or _spec(),),
        agent_allowed_tools=(tool_id,) if agent_allowed_tools is None else agent_allowed_tools,
        model_allowed_classifications=("public", "internal"),
    )


def _spec(
    *,
    tool_id: str = "ontology.get_object",
    effect: ToolEffect = "READ",
    confirmation_policy: ConfirmationPolicy = "NONE",
    result_classification: str = "internal",
    max_result_items: int = 50,
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="2026-06-25",
        input_schema={
            "type": "object",
            "required": ["object_type", "object_id"],
            "additionalProperties": False,
            "properties": {
                "object_type": {"type": "string"},
                "object_id": {"type": "string"},
                "property_names": {"type": "array"},
            },
        },
        output_schema={"type": "object"},
        effect=effect,
        required_permission="object:read",
        confirmation_policy=confirmation_policy,
        object_type_allowlist=("PurchaseOrder",),
        property_allowlist=("id", "status", "supplier", "margin"),
        result_classification=result_classification,
        max_result_items=max_result_items,
    )


def _args(*, properties: tuple[str, ...] = ("id",)) -> str:
    return json.dumps(
        {"object_type": "PurchaseOrder", "object_id": "PO-1042", "property_names": list(properties)},
        separators=(",", ":"),
    )


def _classification_provider(tenant_id: str) -> list[Mapping[str, object]]:
    assert tenant_id == "tenant-demo"
    return [
        {
            "object_type_api_name": "PurchaseOrder",
            "property_api_name": "margin",
            "column_name": "margin",
            "classification": "finance",
        }
    ]


class _SpyExecutor:
    profile_name = "spy-tool-executor"

    def __init__(self, output: Mapping[str, object] | None = None) -> None:
        self.output = output or {"ok": True}
        self.calls = 0
        self.contexts: list[RequestContext] = []

    def execute(self, ctx: RequestContext, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.calls += 1
        self.contexts.append(ctx)
        return ToolExecutionResult(output_json=self.output, result_ref=f"spy://{request.tool_id}")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())
