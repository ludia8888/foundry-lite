from __future__ import annotations

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.trained_model_inference import TrainedModelInvocation
from foundry_lite.infrastructure.adapters.container_trained_model_inference import (
    ContainerTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.container_trained_model_runtime import (
    ContainerTrainedModelConfig,
)


def test_live_trained_model_sidecar_scores_rows_and_proves_sandbox() -> None:
    adapter = ContainerTrainedModelInferenceAdapter(ContainerTrainedModelConfig())

    result = adapter.infer(
        TrainedModelInvocation(
            model_ref="demo.transaction-risk",
            branch="master",
            fallback_branches=(),
            rows=(
                {"amount": 1_000.0, "country": "US"},
                {"amount": 18_000.0, "country": "US"},
            ),
        )
    )

    assert result.rows == (
        {"riskScore": 0.05, "decision": "allow"},
        {"riskScore": 0.8, "decision": "review"},
    )
    assert result.runtime_evidence["uid"] == 65532
    assert result.runtime_evidence["gid"] == 65532
    assert result.runtime_evidence["networkBlocked"] is True
    assert result.runtime_evidence["rootWriteBlocked"] is True
    assert result.runtime_evidence["outputDirectoryWriteBlocked"] is True
    assert result.runtime_evidence["effectiveCapabilities"] == "0000000000000000"
    assert result.runtime_evidence["noNewPrivileges"] == "1"
    assert result.runtime_evidence["runtime"] == "isolated_container_sidecar"


def test_live_trained_model_sidecar_failure_is_typed_and_redacted() -> None:
    adapter = ContainerTrainedModelInferenceAdapter(ContainerTrainedModelConfig())

    with pytest.raises(AdapterError) as captured:
        adapter.infer(
            TrainedModelInvocation(
                model_ref="demo.transaction-risk",
                branch="master",
                fallback_branches=(),
                rows=({"amount": "private-invalid-amount"},),
            )
        )

    evidence = captured.value.failure.details["trainedModelSidecar"]
    assert isinstance(evidence, dict)
    assert captured.value.failure.kind == "validation"
    assert evidence["failureType"] == "model_execution_error"
    assert evidence["exceptionType"] == "ValueError"
    assert "private-invalid-amount" not in str(captured.value.failure.details)
