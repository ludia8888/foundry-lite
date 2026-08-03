from __future__ import annotations

from foundry_lite.application.ports.action_function_executor import ActionFunctionExecutionRequest
from foundry_lite.infrastructure.adapters.action_function_executor import LogicDagActionFunctionExecutor


def test_action_function_executor_contract_returns_typed_edit_batch() -> None:
    adapter = LogicDagActionFunctionExecutor()
    adapter.register_driver(
        lambda request: {
            "logicRunId": f"logic:{request.run_id}",
            "resultHash": "sha256:result",
            "output": {
                "value": {
                    "edits": [
                        {
                            "kind": "modifyObject",
                            "objectType": "Order",
                            "objectId": "O-1",
                            "expectedVersion": 1,
                            "patch": {"status": "DONE"},
                        }
                    ]
                }
            },
        }
    )

    result = adapter.execute(_request())

    assert result.external_execution_id == "logic:run-1"
    assert result.edit_batch.edits[0]["kind"] == "modifyObject"
    assert result.provenance["functionVersion"] == "v7"


def _request() -> ActionFunctionExecutionRequest:
    return ActionFunctionExecutionRequest(
        tenant_id="tenant-a",
        run_id="run-1",
        request_id="req-1",
        actor_user_id="user-1",
        roles=("ops_manager",),
        token_scopes=(),
        application_id=None,
        client_id=None,
        ontology_version_id="ont-4",
        function_api_name="approveOrder",
        function_version="v7",
        inputs={},
        effect_outputs={"effectId": "erp-write", "response": {"approvalCode": "A-1"}},
    )
