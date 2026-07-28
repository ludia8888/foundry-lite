from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineDeploymentRow,
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineVersionRow
from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelField,
    TrainedModelInferencePort,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.services.pipeline_deployment_service import (
    PipelineDeploymentService,
    _engine_neutral_plan_summary,
    _function_pins,
    _has_dataset_source,
    _json_object,
    _json_rows,
    _json_texts,
    _optional_text,
    _processor_pins,
    _require_matching_deployment,
)
from foundry_lite.application.services.pipeline_run_requests import deployed_pipeline_version
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


class _ModelPort:
    def __init__(self, definition: TrainedModelDefinition) -> None:
        self.definition = definition
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def resolve(
        self,
        model_ref: str,
        *,
        branch: str,
        fallback_branches: Sequence[str] = (),
    ) -> TrainedModelDefinition:
        self.calls.append((model_ref, branch, tuple(fallback_branches)))
        return self.definition


def _definition() -> TrainedModelDefinition:
    return TrainedModelDefinition(
        model_ref="demo.risk",
        display_name="Risk",
        branch="master",
        version="7",
        revision="rev-7",
        input_fields=(TrainedModelField("amount", "double"),),
        output_fields=(TrainedModelField("riskScore", "double"),),
    )


def _trained_model_graph(config: object) -> dict[str, object]:
    return {
        "metadata": {"reusables": {"trainedModels": ["demo.risk"]}},
        "nodes": [
            {
                "descriptorId": "transform.trained_model",
                "config": config,
            },
            None,
        ],
    }


def test_deployment_resolves_and_pins_imported_trained_model() -> None:
    port = _ModelPort(_definition())
    service = object.__new__(PipelineDeploymentService)
    service.trained_model_inference_port = cast(TrainedModelInferencePort, port)
    config = {
        "modelRef": "demo.risk",
        "modelBranch": "master",
        "fallbackBranches": ["staging"],
        "inputMappings": {"amount": "$amount"},
        "outputMappings": {"riskScore": "risk"},
    }

    refs = service._trained_model_refs(_trained_model_graph(config))

    assert [(ref.model_id, ref.model_version, ref.revision) for ref in refs] == [("demo.risk", "7", "rev-7")]
    assert port.calls == [("demo.risk", "master", ("staging",))]
    assert refs[0].parameters_fingerprint


@pytest.mark.parametrize("nodes", [None, {}, "invalid"])
def test_deployment_ignores_non_list_node_collections(nodes: object) -> None:
    service = object.__new__(PipelineDeploymentService)

    assert service._trained_model_refs({"nodes": nodes}) == ()
    assert _has_dataset_source({"nodes": nodes}) is False


def test_deployment_rejects_trained_model_node_with_invalid_config() -> None:
    service = object.__new__(PipelineDeploymentService)

    with pytest.raises(ValidationFailed, match="node config"):
        service._trained_model_refs(_trained_model_graph([]))


def test_deployment_helpers_filter_invalid_rows_and_build_runtime_summary() -> None:
    plan: dict[str, object] = {
        "graphSchemaVersion": 2,
        "planFingerprint": "sha256:plan",
        "nodes": [
            {
                "nodeId": "media",
                "descriptorId": "transform.media",
                "specVersion": 2,
                "runtimeCapability": "media_document",
            },
            {
                "nodeId": "python",
                "descriptorId": "transform.python",
                "runtimeCapability": "tabular",
                "config": {"entrypoint": "main"},
            },
            "invalid",
        ],
        "edges": [{"id": "edge-1"}, None],
        "artifacts": [{"id": "artifact-1"}, 3],
        "resultArtifactIds": ["artifact-1", 4],
    }

    summary = _engine_neutral_plan_summary("pipe-1", "version-1", plan)

    assert summary["runtimeCapabilities"] == ["media_document", "tabular"]
    assert summary["nodeCount"] == 2
    assert summary["edgeCount"] == 1
    assert summary["artifactCount"] == 1
    assert summary["resultArtifactIds"] == ["artifact-1"]
    assert _processor_pins(plan) == [{"descriptorId": "transform.media", "specVersion": 2}]
    assert _function_pins(plan)[0]["nodeId"] == "python"
    assert _json_rows(None) == []
    assert _json_texts(None) == []
    assert _json_object(None) == {}
    assert _optional_text({"rollback": " dep-1 "}, "rollback") == "dep-1"
    assert _optional_text({"rollback": " "}, "rollback") is None


def test_deployment_helpers_detect_dataset_sources_and_idempotency_conflicts() -> None:
    assert _has_dataset_source({"nodes": [{"type": "dataset"}]}) is True
    assert _has_dataset_source({"nodes": [{"descriptorId": "source.stream"}]}) is True
    row = cast(PipelineDeploymentRow, {"id": "deployment-1", "request_fingerprint": "expected"})

    _require_matching_deployment(row, "expected")
    with pytest.raises(ConflictDetected) as captured:
        _require_matching_deployment(row, "different")

    assert captured.value.details["deployment_id"] == "deployment-1"


def test_implicit_pipeline_run_uses_latest_promoted_deployment_after_rollback() -> None:
    versions = {
        "version-1": cast(
            PipelineVersionRow,
            {
                "id": "version-1",
                "pipeline_id": "pipe-1",
                "deployed_at": "2026-07-28T01:00:00Z",
            },
        ),
        "version-2": cast(
            PipelineVersionRow,
            {
                "id": "version-2",
                "pipeline_id": "pipe-1",
                "deployed_at": "2026-07-28T02:00:00Z",
            },
        ),
    }
    repository = _PipelineVersionRepository(versions)
    execution_repository = _CurrentDeploymentRepository("version-1")

    selected = deployed_pipeline_version(
        cast(TransactionManager, _TransactionManager()),
        cast(PipelineRepository, repository),
        cast(PipelineExecutionRepository, execution_repository),
        RequestContext(tenant_id="tenant-1"),
        "pipe-1",
        None,
    )

    assert selected["id"] == "version-1"


class _TransactionManager:
    def begin(self) -> _Transaction:
        return _Transaction()


class _Transaction:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


class _PipelineVersionRepository:
    def __init__(self, versions: dict[str, PipelineVersionRow]) -> None:
        self._versions = versions

    def version_by_id(self, **kwargs: object) -> PipelineVersionRow | None:
        return self._versions.get(str(kwargs["version_id"]))

    def list_versions(self, **_kwargs: object) -> list[PipelineVersionRow]:
        return list(reversed(self._versions.values()))


class _CurrentDeploymentRepository:
    def __init__(self, version_id: str) -> None:
        self._version_id = version_id

    def list_deployments(self, **_kwargs: object) -> list[PipelineDeploymentRow]:
        return [
            cast(
                PipelineDeploymentRow,
                {
                    "id": "deployment-3",
                    "pipeline_id": "pipe-1",
                    "version_id": self._version_id,
                    "deployment_number": 3,
                    "status": "PROMOTED",
                },
            )
        ]
