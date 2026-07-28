"""Graph v2 trained-model mapping, execution, and model-pin evidence."""

from collections.abc import Mapping
from dataclasses import replace

import pytest
from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelField,
    TrainedModelInvocation,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_execution_contracts import ModelRef
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    imported_trained_model_refs,
    map_trained_model_inputs,
    merge_trained_model_outputs,
    require_trained_model_import,
    trained_model_branch_config,
    trained_model_definition_payload,
    trained_model_definition_snapshot,
    validate_trained_model_config,
)
from foundry_lite.application.services.pipeline_trained_model_snapshot import (
    trained_model_definition_from_snapshot,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
)
from foundry_lite.application.services.pipeline_v2_runtime_trained_model import (
    PipelineV2TrainedModelRuntime,
)
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure.adapters.local_trained_model_inference import (
    LocalTrainedModelInferenceAdapter,
)


def test_trained_model_runtime_maps_expressions_and_aliases_output() -> None:
    invocations: list[TrainedModelInvocation] = []

    class RecordingAdapter(LocalTrainedModelInferenceAdapter):
        def infer(self, invocation: TrainedModelInvocation):
            invocations.append(invocation)
            return super().infer(invocation)

    node = _trained_model_node()
    runtime = PipelineV2TrainedModelRuntime(
        adapter=RecordingAdapter(),
        run_id="prun_model_1",
        model_refs=(_model_pin(node.config),),
    )

    result = runtime.execute(node, {"input": (_source_artifact(),)})

    assert result.items[0]["risk_score"] == 0.8
    assert result.items[0]["decision"] == "review"
    assert result.manifest["modelPin"] == {
        "modelRef": "demo.transaction-risk",
        "branch": "master",
        "resolvedVersion": "2026.07.1",
        "revision": "container-risk-model-r1",
        "executableReference": "local://demo.transaction-risk@container-risk-model-r1",
    }
    assert result.manifest["previewSupported"] is False
    assert invocations[0].expected_model_version == "2026.07.1"
    assert invocations[0].expected_revision == "container-risk-model-r1"
    assert invocations[0].expected_executable_reference == "local://demo.transaction-risk@container-risk-model-r1"


def test_trained_model_runtime_rejects_executable_drift_from_deployment_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _trained_model_node()
    adapter = LocalTrainedModelInferenceAdapter()
    monkeypatch.setattr(adapter, "infer", lambda _invocation: pytest.fail("drifted model must not execute"))
    stale_pin = replace(_model_pin(node.config), executable_reference="local://previous-executable@sha256:old")
    runtime = PipelineV2TrainedModelRuntime(
        adapter=adapter,
        run_id="prun_model_drift",
        model_refs=(stale_pin,),
    )

    with pytest.raises(ValidationFailed, match="execution-plan pin") as raised:
        runtime.execute(node, {"input": (_source_artifact(),)})

    assert raised.value.details["expected"]["executableReference"] == "local://previous-executable@sha256:old"
    assert (
        raised.value.details["actual"]["executableReference"] == "local://demo.transaction-risk@container-risk-model-r1"
    )


def test_trained_model_runtime_uses_deployed_definition_after_registry_removal() -> None:
    node = _trained_model_node()
    adapter = LocalTrainedModelInferenceAdapter()
    adapter.resolve = lambda *_args, **_kwargs: pytest.fail(  # type: ignore[method-assign]
        "deployed model execution must not require the current registry"
    )
    runtime = PipelineV2TrainedModelRuntime(
        adapter=adapter,
        run_id="prun_historical_model",
        model_refs=(_model_pin(node.config),),
    )

    result = runtime.execute(node, {"input": (_source_artifact(),)})

    assert result.manifest["modelPin"] == {
        "modelRef": "demo.transaction-risk",
        "branch": "master",
        "resolvedVersion": "2026.07.1",
        "revision": "container-risk-model-r1",
        "executableReference": "local://demo.transaction-risk@container-risk-model-r1",
    }


def test_trained_model_runtime_rejects_ambiguous_entrypoint_snapshots() -> None:
    node = _trained_model_node()
    first = _model_pin(node.config)
    second_snapshot = {
        **dict(first.definition_snapshot),
        "executableEntrypoint": "/srv/models/rotated_runner.py",
    }
    second = replace(first, definition_snapshot=second_snapshot)
    runtime = PipelineV2TrainedModelRuntime(
        adapter=LocalTrainedModelInferenceAdapter(),
        run_id="prun_ambiguous_entrypoint",
        model_refs=(first, second),
    )

    with pytest.raises(InvariantViolation, match="no unique matching") as raised:
        runtime.execute(node, {"input": (_source_artifact(),)})

    assert raised.value.details["matchingPinCount"] == 2


def test_trained_model_definition_snapshot_round_trips_and_rejects_malformed_data() -> None:
    definition = _definition()

    assert trained_model_definition_from_snapshot(trained_model_definition_snapshot(definition)) == definition
    with pytest.raises(ValidationFailed, match="snapshot") as raised:
        trained_model_definition_from_snapshot({"modelRef": definition.model_ref})

    assert raised.value.details["field"] == "displayName"


def test_trained_model_config_requires_unique_output_aliases() -> None:
    adapter = LocalTrainedModelInferenceAdapter()
    definition = adapter.resolve("demo.transaction-risk", branch="master")

    with pytest.raises(ValidationFailed, match="unique aliases"):
        validate_trained_model_config(
            {
                "inputMappings": {"amount": "$amount"},
                "outputMappings": {"riskScore": "result", "decision": "result"},
            },
            definition,
        )


def test_trained_model_reusable_import_parser_is_strict_and_normalized() -> None:
    assert imported_trained_model_refs({}) == frozenset()
    assert imported_trained_model_refs({"metadata": []}) == frozenset()
    assert imported_trained_model_refs({"metadata": {"reusables": []}}) == frozenset()
    assert imported_trained_model_refs(
        {
            "metadata": {
                "reusables": {
                    "trainedModels": [" demo.risk ", "", 4, "demo.risk"],
                }
            }
        }
    ) == frozenset({"demo.risk"})

    with pytest.raises(ValidationFailed, match="imported"):
        require_trained_model_import({}, "demo.risk")
    require_trained_model_import(
        {"metadata": {"reusables": {"trainedModels": ["demo.risk"]}}},
        "demo.risk",
    )


def test_trained_model_definition_payload_and_branch_fallbacks_are_complete() -> None:
    definition = _definition()

    payload = trained_model_definition_payload(definition)

    assert payload["modelRef"] == "demo.risk"
    assert payload["inputSchema"] == [
        {"name": "amount", "type": "double", "required": True},
        {"name": "country", "type": "string", "required": False},
    ]
    assert payload["resourceProfile"] == {
        "cpuCores": 2.0,
        "memoryMiB": 1024,
        "gpuType": "none",
        "startupTimeoutSeconds": 30,
    }
    assert trained_model_branch_config({}) == ("master", ())
    assert trained_model_branch_config({"modelBranch": " feature ", "fallbackBranches": [" master ", "", 4]}) == (
        "feature",
        ("master", "4"),
    )


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ({"inputMappings": [], "outputMappings": {}}, "mapping"),
        ({"inputMappings": {}, "outputMappings": {}}, "required inputs"),
        (
            {
                "inputMappings": {"amount": "$amount"},
                "outputMappings": {"riskScore": "risk"},
            },
            "unique aliases",
        ),
        (
            {
                "inputMappings": {"amount": "$amount"},
                "outputMappings": {"riskScore": "", "decision": "decision"},
            },
            "unique aliases",
        ),
    ],
)
def test_trained_model_config_rejects_incomplete_mapping_contracts(
    config: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValidationFailed, match=match):
        validate_trained_model_config(config, _definition())


def test_trained_model_config_rejects_unsupported_model_api_type() -> None:
    definition = replace(
        _definition(),
        input_fields=(TrainedModelField("amount", "unsupported"),),
    )

    with pytest.raises(ValidationFailed, match="unsupported types") as raised:
        validate_trained_model_config(
            {
                "inputMappings": {"amount": "$amount"},
                "outputMappings": {"riskScore": "risk", "decision": "decision"},
            },
            definition,
        )

    assert raised.value.details["unsupportedTypes"] == ["unsupported"]


def test_trained_model_mapping_supports_nested_paths_and_bounded_casts() -> None:
    definition = _definition()
    config = {
        "inputMappings": {
            "amount": "cast($payment.amount as double)",
            "country": "cast($country as string)",
        },
        "outputMappings": {"riskScore": "risk", "decision": "decision"},
    }

    mapped = map_trained_model_inputs(
        [{"payment": {"amount": "42.5"}, "country": 7}],
        config,
        definition,
    )
    assert mapped == ({"amount": 42.5, "country": "7"},)

    integer_definition = replace(
        _definition(),
        input_fields=(TrainedModelField("amount", "integer"),),
    )
    assert map_trained_model_inputs(
        [{"amount": "42"}],
        {
            "inputMappings": {"amount": "cast($amount as integer)"},
            "outputMappings": {"riskScore": "risk", "decision": "decision"},
        },
        integer_definition,
    ) == ({"amount": 42},)


@pytest.mark.parametrize(
    "expression",
    [
        "amount",
        "$missing",
        "cast($amount)",
        "cast($amount as boolean)",
    ],
)
def test_trained_model_mapping_rejects_unsupported_or_missing_expressions(expression: str) -> None:
    config = {
        "inputMappings": {"amount": expression},
        "outputMappings": {"riskScore": "risk", "decision": "decision"},
    }
    with pytest.raises(ValidationFailed):
        map_trained_model_inputs([{"amount": 42}], config, _definition())

    numeric_config = {
        "inputMappings": {"amount": "cast($amount as double)"},
        "outputMappings": {"riskScore": "risk", "decision": "decision"},
    }
    with pytest.raises(ValidationFailed, match="numeric cast source"):
        map_trained_model_inputs([{"amount": True}], numeric_config, _definition())


def test_trained_model_output_merge_enforces_row_and_schema_cardinality() -> None:
    config = {
        "inputMappings": {"amount": "$amount"},
        "outputMappings": {"riskScore": "risk", "decision": "decision"},
    }
    definition = _definition()

    assert merge_trained_model_outputs(
        [{"id": 1}],
        [{"riskScore": 0.8, "decision": "review"}],
        config,
        definition,
    ) == ({"id": 1, "risk": 0.8, "decision": "review"},)

    with pytest.raises(ValidationFailed, match="row count"):
        merge_trained_model_outputs([{"id": 1}], [], config, definition)
    with pytest.raises(ValidationFailed, match="columns") as raised:
        merge_trained_model_outputs(
            [{"id": 1}],
            [{"riskScore": 0.8, "extra": True}],
            config,
            definition,
        )
    assert raised.value.details == {"missing": ["decision"], "extra": ["extra"]}


def _definition() -> TrainedModelDefinition:
    return TrainedModelDefinition(
        model_ref="demo.risk",
        display_name="Risk",
        branch="master",
        version="1",
        revision="r1",
        executable_reference="local://demo.risk@r1",
        input_fields=(
            TrainedModelField("amount", "double"),
            TrainedModelField("country", "string", is_required=False),
        ),
        output_fields=(
            TrainedModelField("riskScore", "double"),
            TrainedModelField("decision", "string"),
        ),
        cpu_cores=2.0,
        memory_mib=1024,
        startup_timeout_seconds=30,
    )


def _trained_model_node() -> PipelineV2RuntimeNode:
    return PipelineV2RuntimeNode(
        node_id="model",
        kind="transform",
        descriptor_id="transform.trained_model",
        spec_version=1,
        runtime_capability="trained_model_batch_runtime",
        config={
            "modelRef": "demo.transaction-risk",
            "modelBranch": "feature/model-api",
            "fallbackBranches": ["master"],
            "inputMappings": {"amount": "$usd_amount", "country": "$country"},
            "outputMappings": {"riskScore": "risk_score", "decision": "decision"},
        },
    )


def _model_pin(config: Mapping[str, object]) -> ModelRef:
    definition = LocalTrainedModelInferenceAdapter().resolve(
        "demo.transaction-risk",
        branch="feature/model-api",
        fallback_branches=("master",),
    )
    return ModelRef(
        model_id="demo.transaction-risk",
        model_version="2026.07.1",
        provider="trained_model",
        revision="container-risk-model-r1",
        parameters_fingerprint=_json_hash(dict(config)),
        executable_reference="local://demo.transaction-risk@container-risk-model-r1",
        definition_snapshot=trained_model_definition_snapshot(definition),
    )


def _source_artifact() -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id="source",
        descriptor_id="source.dataset",
        spec_version=1,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        items=({"id": "tx-1", "usd_amount": 18_000.0, "country": "US"},),
        artifact_ref={"datasetRef": "raw.transactions", "versionId": "dv_1"},
        manifest={"rowCount": 1},
        security_envelope={"classification": "INTERNAL"},
        status="COMMITTED",
        is_serving=True,
    )
