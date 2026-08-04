"""Logic DAG adapter for compute-only, version-pinned Action functions."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from foundry_lite.application.ports.action_function_executor import (
    ActionFunctionExecutionRequest,
    ActionFunctionExecutionResult,
)
from foundry_lite.domain.action_runtime.ontology_edit_batch import OntologyEditBatch

ActionFunctionDriver = Callable[[ActionFunctionExecutionRequest], Mapping[str, object]]


class LogicDagActionFunctionExecutor:
    """Adapter that delegates reads/compute to the existing Logic DAG runtime."""

    profile_name = "logic-dag-action-function"

    def __init__(self) -> None:
        self._driver: ActionFunctionDriver | None = None

    def register_driver(self, driver: ActionFunctionDriver) -> None:
        self._driver = driver

    def execute(self, request: ActionFunctionExecutionRequest) -> ActionFunctionExecutionResult:
        if self._driver is None:
            raise RuntimeError("Action function Logic DAG driver is not registered")
        result = self._driver(request)
        output = result.get("output")
        if not isinstance(output, Mapping):
            raise ValueError("Action function must return an OntologyEditBatch object")
        if isinstance(output.get("value"), Mapping):
            output = output["value"]
        batch = OntologyEditBatch.from_payload(output)
        external_id = str(result.get("logicRunId") or f"action-function:{request.run_id}")
        result_hash = str(result.get("resultHash") or "")
        provenance = {
            "functionApiName": request.function_api_name,
            "functionVersion": request.function_version,
            "ontologyVersionId": request.ontology_version_id,
            "logicRunId": external_id,
            "resultHash": result_hash,
        }
        return ActionFunctionExecutionResult(batch, external_id, result_hash, provenance)
