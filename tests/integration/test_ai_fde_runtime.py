"""AI FDE branch-only authoring, approval, proposal, and ledger evidence."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.application.services.aip.fde_catalog import fde_tool_manifest
from foundry_lite.application.services.aip.fde_ontology_tools import FdeOntologyToolError, FdeOntologyToolRequest
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from sqlalchemy import func, select

FDE_USER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="fde-user",
    roles=("data_engineer",),
    request_id="req-ai-fde",
)
VIEWER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="viewer",
    roles=("viewer",),
    request_id="req-ai-fde-viewer",
)


class _FdeToolThenAnswerModel:
    profile_name = "fde-tool-then-answer"

    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.offered_tools: tuple[str, ...] = ()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.offered_tools = request.tools
        if request.model_call_attempt == 1:
            return _tool_call_response(self.tool_name, self.arguments)
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="The governed branch operation completed; production was not changed.",
            finish_reason="stop",
            input_tokens=7,
            output_tokens=8,
            normalized_tool_calls=(),
            provider_request_id="fde-answer-2",
        )


class _FdeMultiStepModel:
    profile_name = "fde-multi-step"

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.model_call_attempt == 1:
            return _tool_call_response("ontology.branch.inspect", {})
        if request.model_call_attempt == 2:
            return _tool_call_response("ontology.branch.apply_patch", _restaurant_patch())
        if request.model_call_attempt == 3:
            return _tool_call_response("ontology.branch.validate", {})
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="The branch was inspected, edited, and validated; production remains unchanged.",
            finish_reason="stop",
            input_tokens=7,
            output_tokens=8,
            normalized_tool_calls=(),
            provider_request_id="fde-multi-final",
        )


class _FdeRepeatingWriteModel:
    profile_name = "fde-repeating-write"

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        return _tool_call_response("ontology.branch.apply_patch", _restaurant_patch())


class _FdeAnswerModel:
    profile_name = "fde-answer"

    def __init__(self) -> None:
        self.last_request: ModelRequest | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.last_request = request
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="I used the explicitly attached, permission-checked dataset metadata.",
            finish_reason="stop",
            input_tokens=7,
            output_tokens=8,
            normalized_tool_calls=(),
            provider_request_id="fde-answer",
        )


def test_ai_fde_requires_explicit_write_approval_and_never_edits_main(foundry: Any, tmp_path: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="ai-fde", idempotency_key="ai-fde-branch", ctx=FDE_USER)
    branch_id = str(branch["id"])
    model = _FdeToolThenAnswerModel("ontology.branch.apply_patch", _restaurant_patch())
    foundry._services.model_gateway.language_model_adapter = model

    denied = foundry.aip.run_fde_payload(payload=_fde_payload(branch_id, approved=()), ctx=FDE_USER)

    assert denied.result.run_status == "failed"
    assert denied.result.error == {
        "reason": "tool_approval_required",
        "detail": f"explicit user approval is required for ontology.branch.apply_patch on branch {branch_id}",
    }
    assert foundry.ontology.branch_diff(branch_id, ctx=FDE_USER)["resources"] == []

    approved = foundry.aip.run_fde_payload(
        payload=_fde_payload(branch_id, approved=("ontology.branch.apply_patch",), run_id="ai-fde-approved"),
        ctx=FDE_USER,
    )

    assert approved.result.run_status == "succeeded"
    assert "ontology.branch.apply_patch@v1" in model.offered_tools
    diff = foundry.ontology.branch_diff(branch_id, ctx=FDE_USER)
    assert _has_added_restaurant(diff)
    assert not _active_object_type_exists(foundry, "Restaurant")
    tool = _tool_ledger(foundry, approved.result.ai_run_id or "")
    assert tool["authorization_decision"] == "allowed_by_user_preapproval"


def test_ai_fde_runs_bounded_multi_tool_observe_adjust_loop(foundry: Any, tmp_path: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="ai-fde-loop", idempotency_key="ai-fde-loop", ctx=FDE_USER)
    branch_id = str(branch["id"])
    foundry._services.model_gateway.language_model_adapter = _FdeMultiStepModel()

    outcome = foundry.aip.run_fde_payload(
        payload=_fde_payload(branch_id, approved=("ontology.branch.apply_patch",), run_id="ai-fde-loop-run"),
        ctx=FDE_USER,
    )

    assert outcome.result.run_status == "succeeded"
    assert outcome.result.usage is not None
    assert outcome.result.usage["modelCallCount"] == 4
    assert outcome.result.usage["toolCallCount"] == 3
    assert _tool_sequences(foundry, outcome.result.ai_run_id or "") == [1, 2, 3]
    assert _has_added_restaurant(foundry.ontology.branch_diff(branch_id, ctx=FDE_USER))
    assert not _active_object_type_exists(foundry, "Restaurant")


def test_ai_fde_consumes_each_write_tool_approval_once(foundry: Any, tmp_path: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(
        name="ai-fde-single-use-approval", idempotency_key="ai-fde-single-use-approval", ctx=FDE_USER
    )
    foundry._services.model_gateway.language_model_adapter = _FdeRepeatingWriteModel()

    outcome = foundry.aip.run_fde_payload(
        payload=_fde_payload(
            str(branch["id"]), approved=("ontology.branch.apply_patch",), run_id="ai-fde-single-use-run"
        ),
        ctx=FDE_USER,
    )

    assert outcome.result.run_status == "failed"
    assert outcome.result.error is not None
    assert outcome.result.error["reason"] == "tool_approval_consumed"
    assert _tool_sequences(foundry, outcome.result.ai_run_id or "") == [1]
    assert not _active_object_type_exists(foundry, "Restaurant")


def test_ai_fde_reuses_user_branch_session_and_submits_human_proposal(foundry: Any, tmp_path: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="ai-fde-proposal", idempotency_key="ai-fde-proposal", ctx=FDE_USER)
    branch_id = str(branch["id"])
    foundry._services.model_gateway.language_model_adapter = _FdeToolThenAnswerModel(
        "ontology.branch.apply_patch", _restaurant_patch()
    )
    first = foundry.aip.run_fde_payload(
        payload=_fde_payload(branch_id, approved=("ontology.branch.apply_patch",), run_id="fde-turn-1"),
        ctx=FDE_USER,
    )
    foundry._services.model_gateway.language_model_adapter = _FdeToolThenAnswerModel(
        "ontology.branch.propose",
        {"title": "Restaurant domain", "description": "AI FDE draft", "idempotencyKey": "fde-proposal-1"},
    )

    second = foundry.aip.run_fde_payload(
        payload=_fde_payload(branch_id, approved=("ontology.branch.propose",), run_id="fde-turn-2"),
        ctx=FDE_USER,
    )

    assert first.result.session_id == second.result.session_id
    assert second.result.run_status == "succeeded"
    proposed_branch = foundry.ontology.get_branch(branch_id, ctx=FDE_USER)
    assert proposed_branch["status"] == "open"
    assert proposed_branch["proposalId"]
    assert not _active_object_type_exists(foundry, "Restaurant")
    assert _session_count(foundry, first.result.session_id or "") == 1


def test_ai_fde_catalog_is_permission_scoped_and_lists_current_modes(foundry: Any) -> None:
    catalog = foundry.aip.fde_catalog(ctx=FDE_USER)

    modes = {str(mode["modeId"]): str(mode["availability"]) for mode in catalog["modes"]}
    assert modes["ontology_editing"] == "current"
    assert modes["data_integration"] == "current"
    assert modes["platform_qa"] == "current"
    assert catalog["safetyBoundary"]["writes"] == "governed_scope_only"
    tool_ids = {str(tool["toolId"]) for tool in catalog["tools"]}
    assert len(tool_ids) == 69
    assert {
        "list_resources_in_foundry_folder",
        "get_project_imports",
        "create_foundry_project",
        "search_foundry_projects",
        "query_ontology_objects",
        "aggregate_ontology_objects",
        "get_foundry_dataset_schema",
        "list_dataset_files",
        "get_dataset_stats",
        "get_resource_graph",
        "get_foundry_ontology_rid",
        "search_foundry_ontology",
        "search_foundry_functions",
        "view_foundry_object_type",
        "view_foundry_link_type",
        "view_foundry_action_type",
        "create_or_update_foundry_object_type",
        "create_or_update_foundry_link_type",
        "create_or_update_foundry_action_type",
        "delete_foundry_object_type",
        "delete_foundry_link_type",
        "delete_foundry_action_type",
        "get_ontology_sdk_context",
        "get_ontology_sdk_examples",
        "list_platform_sdk_apis",
        "get_platform_sdk_api_reference",
        "get_python_transforms_documentation",
        "get_typescript_v1_functions_documentation",
        "get_typescript_v2_functions_documentation",
        "get_custom_widget_documentation",
        "get_ml_documentation",
        "get_spark_profile_documentation",
        "get_osdk_react_components_documentation",
        "create_foundry_rest_api_data_source",
        "create_foundry_rest_api_data_source_webhook",
        "view_foundry_rest_api_data_source_webhook",
        "get_or_create_network_egress_policy",
        "get_documentation_summaries",
        "search_foundry_documentation",
        "load_foundry_documentation_page",
        "view_osdk_definition",
        "generate_new_ontology_sdk_version",
        "install_sdk_package",
    } <= tool_ids
    viewer_catalog = foundry.aip.fde_catalog(ctx=VIEWER)
    viewer_modes = {str(mode["modeId"]) for mode in viewer_catalog["modes"]}
    assert "ontology_editing" not in viewer_modes
    assert "platform_qa" in viewer_modes
    assert all(tool["effect"] == "READ" for tool in viewer_catalog["tools"])


def test_ai_fde_hydrates_explicit_dataset_context_with_permission_and_ledger(foundry: Any, tmp_path: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="ai-fde-context", idempotency_key="ai-fde-context", ctx=FDE_USER)
    model = _FdeAnswerModel()
    foundry._services.model_gateway.language_model_adapter = model
    payload = _fde_payload(str(branch["id"]), approved=(), run_id="ai-fde-context-run")
    payload["attachedContextRefs"] = ["dataset:clean.restaurants"]

    outcome = foundry.aip.run_fde_payload(payload=payload, ctx=FDE_USER)

    assert outcome.result.run_status == "succeeded"
    assert len(outcome.result.context_ids) == 1
    assert model.last_request is not None
    assert "clean.restaurants" in " ".join(message.content for message in model.last_request.messages)
    context = _context_rows(foundry, outcome.result.ai_run_id or "")
    assert [(row["source_resource_id"], row["retrieval_method"]) for row in context] == [
        ("dataset:clean.restaurants", "explicit_attachment")
    ]

    invalid = _fde_payload(str(branch["id"]), approved=(), run_id="ai-fde-context-invalid")
    invalid["attachedContextRefs"] = ["file:/private/unknown"]
    with pytest.raises(ValidationFailed, match="unsupported AI FDE context reference"):
        foundry.aip.run_fde_payload(payload=invalid, ctx=FDE_USER)

    wrong_branch = _fde_payload(str(branch["id"]), approved=(), run_id="ai-fde-context-wrong-branch")
    wrong_branch["attachedContextRefs"] = ["ontology-branch:some-other-branch"]
    with pytest.raises(ValidationFailed, match="only its selected ontology branch"):
        foundry.aip.run_fde_payload(payload=wrong_branch, ctx=FDE_USER)


def test_ai_fde_tool_output_is_bounded_before_model_followup(foundry: Any, tmp_path: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="ai-fde-budget", idempotency_key="ai-fde-budget", ctx=FDE_USER)
    inspect_spec = next(
        spec for spec in fde_tool_manifest("ontology_editing", ()) if spec.tool_id == "ontology.branch.inspect"
    )
    request = FdeOntologyToolRequest(
        tool_call_id="fde-budget-tool",
        ai_run_id="fde-budget-run",
        sequence=1,
        branch_id=str(branch["id"]),
        spec=inspect_spec,
        arguments={},
        approved_tool_ids=(),
        max_output_bytes=32,
        occurred_at="2026-08-04T00:00:00Z",
    )

    with pytest.raises(FdeOntologyToolError, match="max_tool_output_bytes") as error:
        foundry._services.fde_ontology_tools.execute(FDE_USER, request)

    assert error.value.reason == "budget_exceeded"

    patch_spec = next(
        spec for spec in fde_tool_manifest("ontology_editing", ()) if spec.tool_id.endswith("apply_patch")
    )
    write_result = foundry._services.fde_ontology_tools.execute(
        FDE_USER,
        FdeOntologyToolRequest(
            tool_call_id="fde-budget-write-tool",
            ai_run_id="fde-budget-write-run",
            sequence=1,
            branch_id=str(branch["id"]),
            spec=patch_spec,
            arguments=_restaurant_patch(),
            approved_tool_ids=(patch_spec.tool_id,),
            max_output_bytes=512,
            occurred_at="2026-08-04T00:00:00Z",
        ),
    )

    assert write_result.output_json["isOutputTruncated"] is True
    assert _has_added_restaurant(foundry.ontology.branch_diff(str(branch["id"]), ctx=FDE_USER))


def test_ai_fde_api_exposes_catalog_and_branch_run(foundry: Any, tmp_path: Any, monkeypatch: Any) -> None:
    _prepare_ontology(foundry, tmp_path)
    branch_name = "fde-api-test"
    replay_key = "fde-branch-test"
    branch = foundry.ontology.create_branch(name=branch_name, idempotency_key=replay_key, ctx=FDE_USER)
    foundry._services.model_gateway.language_model_adapter = _FdeToolThenAnswerModel(
        "ontology.branch.apply_patch", _restaurant_patch()
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    headers = {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "fde-user",
        "X-Roles": "data_engineer",
        "X-Request-ID": "req-ai-fde-api",
    }

    catalog = client.get("/api/aip/fde/catalog", headers=headers)
    response = client.post(
        "/api/aip/fde/run",
        headers=headers,
        json=_fde_payload(str(branch["id"]), approved=("ontology.branch.apply_patch",), run_id="fde-api-1"),
    )

    assert catalog.status_code == 200
    assert catalog.json()["safetyBoundary"]["writes"] == "governed_scope_only"
    assert response.status_code == 200
    assert response.json()["runStatus"] == "succeeded"
    assert response.json()["branchId"] == branch["id"]


def _tool_call_response(tool_name: str, arguments: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        provider="fake",
        resolved_model_id="",
        resolved_model_revision="",
        content="I will use the governed branch tool.",
        finish_reason="tool_calls",
        input_tokens=4,
        output_tokens=4,
        normalized_tool_calls=(
            ModelToolCall(tool_name=tool_name, arguments_json=json.dumps(arguments, sort_keys=True)),
        ),
        provider_request_id="fde-tool-1",
    )


def _prepare_ontology(foundry: Any, tmp_path: Any) -> None:
    csv_path = tmp_path / "restaurants.csv"
    csv_path.write_text("restaurant_id,name\nR-1,Seoul Table\n", encoding="utf-8")
    admin = demo_admin_context()
    foundry.datasets.ensure("clean.restaurants", ctx=admin, primary_key=["restaurant_id"])
    foundry.datasets.upload_csv("clean.restaurants", str(csv_path), ctx=admin)
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=admin)


def _restaurant_patch() -> dict[str, object]:
    return {
        "upsertResources": [
            {
                "kind": "objectType",
                "definition": {
                    "apiName": "Restaurant",
                    "primaryKey": "restaurantId",
                    "backing": {"dataset": "clean.restaurants"},
                    "properties": [
                        {
                            "apiName": "restaurantId",
                            "column": "restaurant_id",
                            "type": "string",
                            "nullable": False,
                            "indexed": True,
                        },
                        {"apiName": "name", "column": "name", "type": "string"},
                    ],
                },
            }
        ],
        "deleteResources": [],
        "changeSummary": "Create the Restaurant object type on the isolated branch.",
    }


def _fde_payload(
    branch_id: str,
    *,
    approved: tuple[str, ...],
    run_id: str = "ai-fde-denied",
) -> dict[str, object]:
    return {
        "userMessage": "Create the Restaurant domain model and validate it.",
        "branchId": branch_id,
        "mode": "ontology_editing",
        "approvedToolIds": list(approved),
        "agentRunId": run_id,
    }


def _has_added_restaurant(diff: dict[str, object]) -> bool:
    resources = diff["resources"]
    return isinstance(resources, list) and any(
        isinstance(item, dict)
        and item.get("kind") == "objectType"
        and item.get("apiName") == "Restaurant"
        and item.get("branchChange") == "added"
        for item in resources
    )


def _active_object_type_exists(foundry: Any, api_name: str) -> bool:
    with foundry.engine.begin() as transaction:
        count = transaction.execute(
            select(func.count()).select_from(db.object_types).where(db.object_types.c.api_name == api_name)
        ).scalar_one()
    return int(count) > 0


def _tool_ledger(foundry: Any, ai_run_id: str) -> dict[str, object]:
    with foundry.engine.begin() as transaction:
        row = (
            transaction.execute(select(db.ai_tool_calls).where(db.ai_tool_calls.c.ai_run_id == ai_run_id))
            .mappings()
            .one()
        )
    return dict(row)


def _session_count(foundry: Any, session_id: str) -> int:
    with foundry.engine.begin() as transaction:
        count = transaction.execute(
            select(func.count()).select_from(db.ai_sessions).where(db.ai_sessions.c.id == session_id)
        ).scalar_one()
    return int(count)


def _tool_sequences(foundry: Any, ai_run_id: str) -> list[int]:
    with foundry.engine.begin() as transaction:
        rows = list(
            transaction.execute(
                select(db.ai_tool_calls.c.sequence)
                .where(db.ai_tool_calls.c.ai_run_id == ai_run_id)
                .order_by(db.ai_tool_calls.c.sequence)
            ).scalars()
        )
    return [int(sequence) for sequence in rows]


def _context_rows(foundry: Any, ai_run_id: str) -> list[dict[str, object]]:
    with foundry.engine.begin() as transaction:
        rows = transaction.execute(
            select(db.ai_context_items).where(db.ai_context_items.c.ai_run_id == ai_run_id)
        ).mappings()
        return [dict(row) for row in rows]
