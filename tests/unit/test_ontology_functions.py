"""Ontology function registry (Functions on Objects): apply, catalog, migration, execution.

Covers YAML validation (duplicate/colliding apiNames, unknown runtime, bad
input types, invalid or write-intent block definitions rejected through the
visual-builder validation surface), catalog and snapshot/rollback round-trips,
migration guard semantics (removal blocks, addition/definition change warn),
and execution through the persisted registry: allowedRoles enforcement (admin
implicit, missing roles admin-only), declared-input validation, OSDK function
execute scope, AI-run ledger evidence, and the HTTP execute route.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.aip.visual_builder import VisualBuilderService
from foundry_lite.application.services.ontology_function_validation import (
    validate_function_inputs,
    validate_ontology_functions,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed
from foundry_lite.domain.ontology.function_types import normalized_function_definition
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch

ORDERS_CSV = "order_id,status,risk_score\nO-1,PENDING,0.9\nO-2,REVIEW,0.4\n"

ADMIN_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo-admin",
    "X-Roles": "admin,data_engineer,ops_manager",
}

OPS_CTX = RequestContext(actor_user_id="ops-user", roles=("ops_manager",))
VIEWER_CTX = RequestContext(actor_user_id="viewer-user", roles=("viewer",))
ADMIN_ONLY_CTX = RequestContext(actor_user_id="admin-user", roles=("admin",))


def _order_type() -> dict[str, object]:
    return {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"dataset": "clean.fn_orders", "mode": "snapshot", "primaryKeyColumns": ["order_id"]},
        "properties": [
            {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "status", "column": "status", "type": "string"},
            {"apiName": "riskScore", "column": "risk_score", "type": "float", "indexed": True},
        ],
    }


def _read_tool() -> dict[str, object]:
    return {
        "toolId": "ontology.get_object",
        "version": "2026-06-25",
        "effect": "READ",
        "confirmationPolicy": "NONE",
        "requiredPermission": "object:read",
        "inputSchema": {
            "type": "object",
            "required": ["object_type", "object_id"],
            "properties": {
                "object_type": {"type": "string"},
                "object_id": {"type": "string"},
                "property_names": {"type": "array"},
            },
        },
        "outputSchema": {"type": "object"},
        "objectTypeAllowlist": ["Order"],
        "propertyAllowlist": ["riskScore", "status"],
        "resultClassification": "internal",
    }


def _read_blocks() -> list[dict[str, object]]:
    return [
        {"blockId": "input", "kind": "Input"},
        {
            "blockId": "read_order",
            "kind": "CallFunction",
            "dependsOn": ["input"],
            "inputs": {
                "toolId": "ontology.get_object",
                "version": "2026-06-25",
                "arguments": {"object_type": "Order", "object_id": "O-1", "property_names": ["riskScore", "status"]},
            },
        },
        {"blockId": "output", "kind": "Output", "dependsOn": ["read_order"], "inputs": {"fromBlock": "read_order"}},
    ]


def _function(**overrides: object) -> dict[str, object]:
    function: dict[str, object] = {
        "apiName": "orderRiskSummary",
        "displayName": "Order risk summary",
        "version": "v1",
        "runtime": "logic_dag",
        "inputs": [{"apiName": "objectId", "type": "string", "required": True}],
        "output": {"type": "string"},
        "permissions": {"allowedRoles": ["ops_manager"]},
        "definition": {"tools": [_read_tool()], "blocks": _read_blocks()},
    }
    function.update(overrides)
    return function


def _definition(functions: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "objectTypes": [_order_type()],
        "functionTypes": functions if functions is not None else [_function()],
    }


def _yaml_text(definition: dict[str, object]) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def test_function_definition_preserves_typed_list_of_struct_batch_input() -> None:
    function = _function(
        inputs=[
            {
                "apiName": "requests",
                "type": "array",
                "itemType": "struct",
                "required": True,
                "fields": [
                    {"apiName": "objectId", "type": "string", "required": True},
                    {"apiName": "priority", "type": "integer", "required": False},
                ],
            }
        ]
    )

    normalized = normalized_function_definition(function)

    assert normalized["inputs"] == function["inputs"]


def _prepare_datasets(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    admin = demo_admin_context()
    (tmp_path / "fn_orders.csv").write_text(ORDERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.fn_orders", ctx=admin, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.fn_orders", str(tmp_path / "fn_orders.csv"), ctx=admin)
    return admin


def _prepare_applied(
    foundry: FoundryLite,
    tmp_path: Path,
    definition: dict[str, object] | None = None,
) -> RequestContext:
    admin = _prepare_datasets(foundry, tmp_path)
    foundry.ontology.apply_text(_yaml_text(definition or _definition()), ctx=admin)
    return admin


def test_normalized_function_definition_is_idempotent_and_applies_defaults() -> None:
    normalized = normalized_function_definition(_function())

    # Replaying a persisted snapshot through the normalizer must be a no-op,
    # otherwise rollback would read as a spurious definition change.
    assert normalized_function_definition(normalized) == normalized
    logic = cast(dict[str, Any], normalized["definition"])
    assert logic["blocks"][0] == {"blockId": "input", "kind": "Input", "inputs": {}, "dependsOn": []}
    tool = logic["tools"][0]
    assert tool["status"] == "published"
    assert tool["timeoutSeconds"] == 30
    assert tool["maxResultItems"] == 50

    with pytest.raises(ValidationFailed, match="function definition must declare blocks"):
        normalized_function_definition(_function(definition={"blocks": []}))
    with pytest.raises(ValidationFailed, match="duplicate function input apiName"):
        normalized_function_definition(
            _function(inputs=[{"apiName": "objectId", "type": "string"}, {"apiName": "objectId", "type": "string"}])
        )


def test_validate_ontology_functions_and_inputs_direct_specification() -> None:
    functions = validate_ontology_functions(_definition(), OPS_CTX, VisualBuilderService())

    assert set(functions) == {"orderRiskSummary"}
    validate_function_inputs(functions["orderRiskSummary"], {"objectId": "O-1"})
    with pytest.raises(ValidationFailed, match="function inputs do not match"):
        validate_function_inputs(functions["orderRiskSummary"], {"objectId": 42})
    with pytest.raises(ValidationFailed, match="function inputs do not match"):
        validate_function_inputs(functions["orderRiskSummary"], {})


def test_apply_publishes_function_types_in_catalog(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_applied(foundry, tmp_path)

    catalog = foundry.ontology.catalog(ctx=admin)

    assert catalog["functionTypes"] == [
        {
            "apiName": "orderRiskSummary",
            "displayName": "Order risk summary",
            "version": "v1",
            "runtime": "logic_dag",
            "inputs": [{"apiName": "objectId", "type": "string", "required": True}],
            "output": {"type": "string"},
            "allowedRoles": ["ops_manager"],
        }
    ]


def test_validation_rejects_duplicate_and_colliding_function_api_names(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)

    with pytest.raises(ValidationFailed, match="duplicate function apiName"):
        foundry.ontology.validate(_yaml_text(_definition([_function(), _function()])), ctx=admin)

    with pytest.raises(ValidationFailed, match="collides with another ontology apiName"):
        foundry.ontology.validate(_yaml_text(_definition([_function(apiName="Order")])), ctx=admin)


def test_validation_rejects_unknown_runtime_and_bad_input_type(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)

    with pytest.raises(ValidationFailed, match="unsupported function runtime"):
        foundry.ontology.validate(_yaml_text(_definition([_function(runtime="typescript_v2")])), ctx=admin)

    bad_input = _function(inputs=[{"apiName": "objectId", "type": "uuid"}])
    with pytest.raises(ValidationFailed, match="unsupported function data type"):
        foundry.ontology.validate(_yaml_text(_definition([bad_input])), ctx=admin)

    bad_output = _function(output={"type": "records"})
    with pytest.raises(ValidationFailed, match="unsupported function data type"):
        foundry.ontology.validate(_yaml_text(_definition([bad_output])), ctx=admin)


def test_validation_rejects_invalid_block_definitions_via_visual_builder(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)

    unknown_kind = _function(definition={"blocks": [{"blockId": "x", "kind": "LaunchMissiles"}]})
    with pytest.raises(ValidationFailed, match="failed logic validation") as unknown_exc:
        foundry.ontology.validate(_yaml_text(_definition([unknown_kind])), ctx=admin)
    assert "unsupported_block" in _issue_codes(unknown_exc.value)

    undeclared_tool = _function(definition={"tools": [], "blocks": _read_blocks()})
    with pytest.raises(ValidationFailed, match="failed logic validation") as tool_exc:
        foundry.ontology.validate(_yaml_text(_definition([undeclared_tool])), ctx=admin)
    assert "missing_tool_manifest" in _issue_codes(tool_exc.value)


def test_write_intent_blocks_are_rejected_in_function_definitions(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)

    apply_action = _function(definition={"blocks": [{"blockId": "apply", "kind": "ApplyAction"}]})
    with pytest.raises(ValidationFailed, match="failed logic validation") as apply_exc:
        foundry.ontology.apply_text(_yaml_text(_definition([apply_action])), ctx=admin)
    assert "direct_apply_action_blocked" in _issue_codes(apply_exc.value)

    proposal = _function(
        definition={
            "blocks": [{"blockId": "propose", "kind": "CreateActionProposal", "inputs": {"actionType": "ApproveOrder"}}]
        }
    )
    with pytest.raises(ValidationFailed, match="failed logic validation") as proposal_exc:
        foundry.ontology.apply_text(_yaml_text(_definition([proposal])), ctx=admin)
    assert "action_not_allowed" in _issue_codes(proposal_exc.value)


def test_migration_guard_blocks_removal_and_warns_on_add_and_change(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_applied(foundry, tmp_path)

    removed_plan = _plan(foundry.ontology.validate(_yaml_text({"objectTypes": [_order_type()]}), ctx=admin))
    assert removed_plan["status"] == "blocked"
    assert any(
        change["kind"] == "function_removed" and change["path"] == "functionTypes.orderRiskSummary"
        for change in cast(list[dict[str, object]], removed_plan["blockedChanges"])
    )

    changed_blocks = _read_blocks()
    cast(dict[str, Any], changed_blocks[1])["inputs"]["arguments"] = {"object_type": "Order", "object_id": "O-2"}
    changed_plan = _plan(
        foundry.ontology.validate(
            _yaml_text(_definition([_function(definition={"tools": [_read_tool()], "blocks": changed_blocks})])),
            ctx=admin,
        )
    )
    assert changed_plan["status"] == "compatible_with_warning"
    assert "function_definition_changed" in _warning_kinds(changed_plan)

    added_plan = _plan(
        foundry.ontology.validate(
            _yaml_text(_definition([_function(), _function(apiName="orderRiskFlag")])),
            ctx=admin,
        )
    )
    assert "function_added" in _warning_kinds(added_plan)


def test_rollback_round_trip_preserves_function_types(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_applied(foundry, tmp_path)
    # v2 changes the function definition (a warning), then rollback restores v1.
    # Dropping a function during rollback would block exactly like a fresh apply.
    second = _definition([_function(permissions={"allowedRoles": ["ops_manager", "viewer"]})])
    foundry.ontology.apply_text(_yaml_text(second), ctx=admin)

    result = foundry.ontology.rollback(1, ctx=admin)

    assert result["version_number"] == 3
    catalog = foundry.ontology.catalog(ctx=admin)
    assert [item["apiName"] for item in catalog["functionTypes"]] == ["orderRiskSummary"]
    assert catalog["functionTypes"][0]["allowedRoles"] == ["ops_manager"]
    assert catalog["functionTypes"][0]["version"] == "v1"
    # Replaying the restored version against itself is change-free: persisted
    # normalization is idempotent, so no spurious definition-change warnings.
    revalidated = foundry.ontology.validate(_yaml_text(_definition()), ctx=admin)
    assert revalidated["migration_plan"]["status"] == "compatible"


def test_execute_function_happy_path_returns_output_and_ai_ledger_evidence(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    _prepare_applied(foundry, tmp_path)

    result = foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=OPS_CTX)

    assert result["functionApiName"] == "orderRiskSummary"
    assert result["status"] == "succeeded"
    assert result["resultHash"].startswith("sha256:")
    output = cast(dict[str, Any], result["output"])
    assert output["status"] == "succeeded"
    assert output["output"]["tool_id"] == "ontology.get_object"
    assert output["output"]["input"]["property_names"] == ["riskScore", "status"]
    ai_run_id = result["aiRunId"]
    assert isinstance(ai_run_id, str)
    ledger = _ledger(foundry.engine, ai_run_id)
    assert [event["event_type"] for event in ledger["events"]] == ["aip.logic.started", "aip.logic.completed"]
    assert ledger["toolCalls"][0]["tool_id"] == "ontology.get_object"
    assert ledger["toolCalls"][0]["authorization_decision"] == "allowed"


def test_execute_pure_compute_function_without_tools(foundry: FoundryLite, tmp_path: Path) -> None:
    compute = _function(
        apiName="echoInputs",
        inputs=[{"apiName": "note", "type": "string", "required": True}],
        permissions={"allowedRoles": ["ops_manager"]},
        definition={
            "blocks": [
                {"blockId": "input", "kind": "Input"},
                {"blockId": "output", "kind": "Output", "dependsOn": ["input"], "inputs": {"fromBlock": "input"}},
            ]
        },
    )
    _prepare_applied(foundry, tmp_path, _definition([compute]))

    result = foundry.functions.execute("echoInputs", inputs={"note": "hello"}, ctx=OPS_CTX)

    assert result["status"] == "succeeded"
    assert result["output"] == {"value": {"note": "hello"}}


def test_execute_function_enforces_allowed_roles_with_admin_implicit(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_applied(foundry, tmp_path)

    with pytest.raises(PermissionDenied, match="permission denied for function execution"):
        foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=VIEWER_CTX)

    admin_result = foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=ADMIN_ONLY_CTX)
    assert admin_result["status"] == "succeeded"


def test_execute_function_without_declared_roles_fails_closed_to_admin(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_applied(foundry, tmp_path, _definition([_function(permissions=None)]))

    with pytest.raises(PermissionDenied) as exc_info:
        foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=OPS_CTX)
    assert exc_info.value.details["allowedRoles"] == ["admin"]

    admin_result = foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=ADMIN_ONLY_CTX)
    assert admin_result["status"] == "succeeded"


def test_execute_function_rejects_unknown_function_and_bad_inputs(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_applied(foundry, tmp_path)

    with pytest.raises(NotFound, match="function type not found"):
        foundry.functions.execute("ghostFunction", inputs={}, ctx=OPS_CTX)

    with pytest.raises(ValidationFailed, match="function inputs do not match"):
        foundry.functions.execute("orderRiskSummary", inputs={}, ctx=OPS_CTX)
    with pytest.raises(ValidationFailed, match="function inputs do not match"):
        foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1", "extra": 1}, ctx=OPS_CTX)
    with pytest.raises(ValidationFailed, match="function inputs do not match"):
        foundry.functions.execute("orderRiskSummary", inputs={"objectId": 42}, ctx=OPS_CTX)


def test_execute_function_requires_osdk_function_execute_scope(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_applied(foundry, tmp_path)
    app_bundle = foundry.developer_console.create_osdk_application(
        app_api_name="FunctionsRuntime",
        display_name="FunctionsRuntime",
        client_id="functions-client",
        resources=({"resourceType": "object", "resourceApiName": "Order", "scopes": ["osdk:object:Order:read"]},),
        idempotency_key="functions-runtime-create",
        ctx=admin,
    )
    denied_ctx = replace(
        admin,
        application_id=app_bundle["application"]["id"],
        client_id="functions-client",
        token_scopes=("osdk:function:orderRiskSummary:execute",),
    )

    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=denied_ctx)

    foundry.developer_console.update_osdk_application_resources(
        app_bundle["application"]["id"],
        resources=(
            {
                "resourceType": "function",
                "resourceApiName": "orderRiskSummary",
                "scopes": ["osdk:function:orderRiskSummary:execute"],
            },
        ),
        idempotency_key="functions-runtime-grant",
        ctx=admin,
    )
    granted = foundry.functions.execute("orderRiskSummary", inputs={"objectId": "O-1"}, ctx=denied_ctx)
    assert granted["status"] == "succeeded"


def test_api_function_execute_endpoint_returns_output(
    foundry: FoundryLite, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _prepare_applied(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    response = client.post(
        "/api/functions/orderRiskSummary/execute",
        headers=ADMIN_HEADERS,
        json={"inputs": {"objectId": "O-1"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["functionApiName"] == "orderRiskSummary"
    assert payload["status"] == "succeeded"
    assert payload["output"]["output"]["tool_id"] == "ontology.get_object"

    missing = client.post("/api/functions/ghostFunction/execute", headers=ADMIN_HEADERS, json={"inputs": {}})
    assert missing.status_code == 404


def _plan(result: object) -> dict[str, Any]:
    return cast(dict[str, Any], cast(dict[str, object], result)["migration_plan"])


def _warning_kinds(plan: dict[str, Any]) -> set[str]:
    return {str(change["kind"]) for change in cast(list[dict[str, object]], plan["warnings"])}


def _issue_codes(error: ValidationFailed) -> set[str]:
    issues = cast(list[dict[str, object]], error.details["issues"])
    return {str(issue["code"]) for issue in issues}


def _ledger(engine: Any, ai_run_id: str) -> dict[str, Any]:
    with engine.begin() as transaction:
        ledger = SqlAlchemyAiRunRepository(engine).ledger_for_run(
            transaction=transaction, tenant_id="tenant-demo", ai_run_id=ai_run_id
        )
    assert ledger is not None
    return cast(dict[str, Any], ledger)


# --- python runtime: source instead of a block graph ---------------------------------


def _python_function(**overrides: object) -> dict[str, object]:
    definition: dict[str, object] = {
        "apiName": "TotalSeats",
        "runtime": "python",
        "inputs": [{"apiName": "tables", "type": "objectSet", "objectType": "DiningTable", "required": True}],
        "output": {"type": "integer"},
        "definition": {"source": "def compute(tables):\n    return len(tables)\n"},
    }
    definition.update(overrides)
    return definition


def test_a_python_function_carries_source_and_defaults_its_entry_point() -> None:
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    normalized = normalized_function_definition(_python_function())

    assert normalized["runtime"] == "python"
    assert normalized["definition"]["entrypoint"] == "compute"
    assert "blocks" not in normalized["definition"]


def test_normalizing_a_python_function_is_idempotent() -> None:
    """Replaying a persisted snapshot must not read as a spurious definition change."""
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    once = normalized_function_definition(_python_function())

    assert normalized_function_definition(once) == once


def test_a_python_function_cannot_smuggle_logic_dag_blocks() -> None:
    """Two runtimes in one definition would leave which one executes ambiguous."""
    from foundry_lite.domain.errors import ValidationFailed
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    with pytest.raises(ValidationFailed, match="cannot declare Logic DAG blocks"):
        normalized_function_definition(
            _python_function(definition={"source": "def compute():\n    return 1\n", "blocks": []})
        )


def test_a_python_function_with_blank_source_is_refused() -> None:
    from foundry_lite.domain.errors import ValidationFailed
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    with pytest.raises(ValidationFailed, match="must not be blank"):
        normalized_function_definition(_python_function(definition={"source": "   \n"}))


def test_a_logic_dag_function_still_requires_blocks() -> None:
    """Adding a second runtime must not relax the first one's contract."""
    from foundry_lite.domain.errors import ValidationFailed
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    with pytest.raises(ValidationFailed, match="must declare blocks"):
        normalized_function_definition(_python_function(runtime="logic_dag", definition={"blocks": []}))


def test_typescript_is_a_code_runtime_like_python() -> None:
    """Palantir offers TypeScript and Python side by side; both carry source, not a block graph."""
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    normalized = normalized_function_definition(
        _python_function(runtime="typescript", definition={"source": "export function compute() { return 1 }"})
    )

    assert normalized["runtime"] == "typescript"
    assert normalized["definition"]["entrypoint"] == "compute"


def test_an_unknown_runtime_is_still_refused() -> None:
    """Adding runtimes must not turn the allowlist into a pass-through."""
    from foundry_lite.domain.errors import ValidationFailed
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    with pytest.raises(ValidationFailed, match="unsupported function runtime"):
        normalized_function_definition(_python_function(runtime="rust"))


def test_a_function_version_carries_its_own_timeout() -> None:
    """Palantir configures timeout per function version, not per function.

    A version is immutable once published, so its budget travels with it: raising the limit means
    publishing a new version, and an Action pinned to the old one keeps the budget it was tested
    against rather than inheriting a change it never saw.
    """
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    assert normalized_function_definition(_python_function())["timeoutSeconds"] == 60
    assert normalized_function_definition(_python_function(timeoutSeconds=120))["timeoutSeconds"] == 120


@pytest.mark.parametrize("timeout", [0, -1, 281])
def test_a_timeout_outside_the_permitted_range_is_refused(timeout: int) -> None:
    """Authored content must not be able to raise the operator's bound on a sandbox slot."""
    from foundry_lite.domain.errors import ValidationFailed
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    with pytest.raises(ValidationFailed, match="outside the permitted range"):
        normalized_function_definition(_python_function(timeoutSeconds=timeout))


def test_optional_input_facets_survive_normalization() -> None:
    """Media and struct inputs carry constraints the runtime relies on; dropping them silently
    would loosen a declared bound rather than fail."""
    from foundry_lite.domain.ontology.function_types import normalized_function_definition

    normalized = normalized_function_definition(
        _python_function(
            inputs=[
                {
                    "apiName": "upload",
                    "type": "media",
                    "required": True,
                    "constraints": {"minBytes": 1},
                    "allowedMimeTypes": ["image/png"],
                    "maxBytes": 4096,
                }
            ]
        )
    )

    declared = normalized["inputs"][0]
    assert declared["constraints"] == {"minBytes": 1}
    assert declared["allowedMimeTypes"] == ["image/png"]
    assert declared["maxBytes"] == 4096
