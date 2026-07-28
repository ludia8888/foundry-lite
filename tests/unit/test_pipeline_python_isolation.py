from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.compute_adapter import PythonTransformPlan
from foundry_lite.application.services.pipeline_preview_executor import (
    execute_pipeline_preview,
)
from foundry_lite.application.services.pipeline_preview_runtime import PipelinePreviewRuntime
from foundry_lite.application.services.pipeline_preview_transforms import (
    PipelineCodeIsolationRequired,
)
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter


@pytest.mark.parametrize(
    ("descriptor_id", "config"),
    [
        (
            "transform.sql",
            {
                "sql": "SELECT read_csv_auto('/etc/passwd')",
                "outputDatasetRef": "preview.unsafe_sql",
            },
        ),
        (
            "transform.python",
            {
                "sourceCode": "open('/tmp/preview-was-executed', 'w').write('unsafe')",
                "outputDatasetRef": "preview.unsafe_python",
            },
        ),
    ],
)
def test_unsaved_code_preview_requires_isolated_worker_without_execution(
    descriptor_id: str,
    config: dict[str, object],
) -> None:
    graph = _code_preview_graph(descriptor_id, config)

    with pytest.raises(PipelineCodeIsolationRequired) as captured:
        execute_pipeline_preview(
            graph,
            preview_run_id="preview-code-isolation",
            target_node_id="code",
            limits={"timeoutSeconds": 30},
            runtime=cast(PipelinePreviewRuntime, object()),
        )

    assert captured.value.code == "PIPELINE_CODE_ISOLATION_REQUIRED"
    assert captured.value.details == {
        "nodeId": "code",
        "descriptorId": descriptor_id,
        "requiredCapability": "isolated_code_execution_worker",
        "executionAttempted": False,
    }


def test_saved_python_transform_cannot_fall_back_to_api_process_execution(tmp_path) -> None:
    marker = tmp_path / "api-process-executed"
    plan = PythonTransformPlan(
        entrypoint=str(tmp_path / "unsafe.py"),
        source_code=f"open({str(marker)!r}, 'w').write('unsafe')\n",
        function_name=None,
        input_refs_by_alias={},
        input_paths_by_ref={},
        output_dataset_ref="preview.unsafe_python",
        target_path=tmp_path / "output.parquet",
    )

    with pytest.raises(AdapterError) as captured:
        DuckDBComputeAdapter().execute_transform(plan)

    assert captured.value.failure.adapter_profile == "code-execution-unconfigured"
    assert captured.value.failure.details["codeExecution"] == {
        "failureType": "runtime_unavailable",
        "executionAttempted": False,
    }
    assert not marker.exists()


def _code_preview_graph(descriptor_id: str, config: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "code",
                "kind": "transform",
                "descriptorId": descriptor_id,
                "specVersion": 1,
                "config": config,
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "preview.out"},
            },
        ],
        "edges": [
            {
                "id": "code-out",
                "sourceNodeId": "code",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            }
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }
