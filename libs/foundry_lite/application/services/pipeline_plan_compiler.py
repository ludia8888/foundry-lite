"""Pure compiler from Pipeline Builder graphs to immutable execution plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from foundry_lite.application.services.pipeline_execution_contracts import (
    ComputeProfile,
    ModelRef,
    PipelineArtifactRef,
    PipelineExecutionPlan,
    PipelinePlanEdge,
    PipelinePlanNode,
    pipeline_plan_fingerprint,
)
from foundry_lite.application.services.pipeline_graph_contracts import (
    JsonObject,
    PipelineGraphV2,
    PipelineNodeAvailability,
    PipelineNodeDescriptor,
    PipelineNodeKind,
    PipelineV2Edge,
    PipelineV2Node,
    pipeline_node_descriptor,
)
from foundry_lite.application.services.pipeline_graph_model import (
    pipeline_graph_fingerprint,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_graph_v2_validation import validate_pipeline_graph_v2
from foundry_lite.application.services.pipeline_runtime_capabilities import (
    DEFAULT_PIPELINE_RUNTIME_CAPABILITIES,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceContract,
    PipelineSourceResolution,
    source_config_without_untrusted_schema,
    source_contracts_by_node,
)
from foundry_lite.application.services.pipeline_source_validation import (
    validate_pipeline_graph_with_sources,
)
from foundry_lite.domain.errors import ValidationFailed

DEFAULT_PIPELINE_COMPILER_VERSION = "pipeline-plan-v2.0"


class PipelinePlanCompilationFailed(ValidationFailed):
    code = "PIPELINE_PLAN_COMPILATION_FAILED"


class PipelineRuntimeUnavailable(PipelinePlanCompilationFailed):
    code = "PIPELINE_RUNTIME_UNAVAILABLE"


class PipelinePlanCompiler:
    """Compile validated v1/v2 graphs without writes or runtime side effects."""

    def __init__(
        self,
        *,
        available_runtime_capabilities: Iterable[str] | None = None,
        compiler_version: str = DEFAULT_PIPELINE_COMPILER_VERSION,
    ) -> None:
        capabilities = available_runtime_capabilities
        self._available_runtime_capabilities = frozenset(
            DEFAULT_PIPELINE_RUNTIME_CAPABILITIES if capabilities is None else capabilities
        )
        self._compiler_version = _required_text(compiler_version, "pipeline compiler version")

    def compile(
        self,
        graph: Mapping[str, object],
        *,
        target_node_ids: Sequence[str] | None = None,
        compute_profile: ComputeProfile | None = None,
        model_refs: Sequence[ModelRef] = (),
        source_resolution: PipelineSourceResolution | None = None,
        require_source_contracts: bool = False,
    ) -> PipelineExecutionPlan:
        canonical, warnings = _validated_canonical_graph(graph, source_resolution)
        targets = _normalized_targets(target_node_ids, canonical["nodes"])
        selected = _selected_node_ids(canonical, targets)
        nodes = self._plan_nodes(canonical, selected)
        edges = _plan_edges(canonical["edges"], selected)
        artifacts = _plan_artifacts(nodes)
        result_ids = _result_artifact_ids(nodes, artifacts, targets)
        models = _sorted_model_refs(model_refs)
        source_contracts = _selected_source_contracts(
            nodes,
            source_resolution,
            require_source_contracts=require_source_contracts,
        )
        graph_fingerprint = pipeline_graph_fingerprint(canonical)
        return _build_execution_plan(
            compiler_version=self._compiler_version,
            graph_fingerprint=graph_fingerprint,
            targets=targets,
            nodes=nodes,
            edges=edges,
            artifacts=artifacts,
            result_ids=result_ids,
            source_contracts=source_contracts,
            compute_profile=compute_profile,
            model_refs=models,
            warnings=_relevant_warnings(warnings, selected),
        )

    def _plan_nodes(
        self,
        graph: PipelineGraphV2,
        selected: frozenset[str],
    ) -> tuple[PipelinePlanNode, ...]:
        node_by_id = {node["id"]: node for node in graph["nodes"]}
        ordered_ids = _topological_node_ids(selected, graph["edges"])
        planned: list[PipelinePlanNode] = []
        for node_id in ordered_ids:
            node = node_by_id[node_id]
            descriptor = _required_descriptor(node)
            self._require_executable(node, descriptor)
            planned.append(_plan_node(node, descriptor, graph["outputContract"]))
        return tuple(planned)

    def _require_executable(
        self,
        node: PipelineV2Node,
        descriptor: PipelineNodeDescriptor,
    ) -> None:
        details = _runtime_failure_details(node, descriptor)
        executable = {
            PipelineNodeAvailability.GRAPH_V2_EXECUTABLE,
            PipelineNodeAvailability.LEGACY_EXECUTABLE,
            PipelineNodeAvailability.GOVERNED_CANDIDATE,
        }
        if descriptor.availability not in executable:
            details["reason"] = "descriptor_validation_only"
            raise PipelineRuntimeUnavailable("pipeline node runtime is not executable", details=details)
        if descriptor.runtime_capability not in self._available_runtime_capabilities:
            details["reason"] = "runtime_capability_not_enabled"
            raise PipelineRuntimeUnavailable("pipeline runtime capability is not enabled", details=details)


_CONTRACT_BEARING_SOURCES = frozenset(  # sources that must carry a contract in an executable plan
    {"source.dataset", "source.geospatial", "source.media_set", "source.stream", "source.virtual_table"}
)


def compile_pipeline_plan(
    graph: Mapping[str, object],
    *,
    target_node_ids: Sequence[str] | None = None,
) -> PipelineExecutionPlan:
    return PipelinePlanCompiler().compile(graph, target_node_ids=target_node_ids)


def _build_execution_plan(
    compiler_version: str,
    graph_fingerprint: str,
    targets: tuple[str, ...],
    nodes: tuple[PipelinePlanNode, ...],
    edges: tuple[PipelinePlanEdge, ...],
    artifacts: tuple[PipelineArtifactRef, ...],
    result_ids: tuple[str, ...],
    source_contracts: tuple[PipelineSourceContract, ...],
    compute_profile: ComputeProfile | None,
    model_refs: tuple[ModelRef, ...],
    warnings: tuple[Mapping[str, object], ...],
) -> PipelineExecutionPlan:
    fingerprint = pipeline_plan_fingerprint(
        compiler_version=compiler_version,
        graph_fingerprint=graph_fingerprint,
        target_node_ids=targets,
        nodes=nodes,
        edges=edges,
        artifacts=artifacts,
        result_artifact_ids=result_ids,
        source_contracts=source_contracts,
        compute_profile=compute_profile,
        model_refs=model_refs,
    )
    return PipelineExecutionPlan(
        compiler_version=compiler_version,
        graph_schema_version=2,
        graph_fingerprint=graph_fingerprint,
        plan_fingerprint=fingerprint,
        target_node_ids=targets,
        nodes=nodes,
        edges=edges,
        artifacts=artifacts,
        result_artifact_ids=result_ids,
        source_contracts=source_contracts,
        validation_warnings=warnings,
        compute_profile=compute_profile,
        model_refs=model_refs,
    )


def _validated_canonical_graph(
    graph: Mapping[str, object],
    source_resolution: PipelineSourceResolution | None,
) -> tuple[PipelineGraphV2, list[JsonObject]]:
    if source_resolution is not None:
        validation = validate_pipeline_graph_with_sources(graph, source_resolution)
        _raise_for_validation(validation, "committed_sources")
        return normalize_pipeline_graph(graph), _json_rows(validation.get("warnings"))
    source_validation = validate_pipeline_graph(graph)
    _raise_for_validation(source_validation, "source")
    canonical = normalize_pipeline_graph(graph)
    canonical_validation = validate_pipeline_graph_v2(canonical)
    _raise_for_validation(canonical_validation, "canonical_v2")
    return canonical, _json_rows(canonical_validation.get("warnings"))


def _raise_for_validation(validation: Mapping[str, object], phase: str) -> None:
    if validation.get("valid") is True:
        return
    raise PipelinePlanCompilationFailed(
        "pipeline graph cannot be compiled",
        details={
            "phase": phase,
            "validationErrors": _json_rows(validation.get("errors")),
        },
    )


def _normalized_targets(
    target_node_ids: Sequence[str] | None,
    nodes: Sequence[PipelineV2Node],
) -> tuple[str, ...]:
    targets = tuple(sorted(set(target_node_ids or ())))
    known = {node["id"] for node in nodes}
    missing = [node_id for node_id in targets if node_id not in known]
    if missing:
        raise PipelinePlanCompilationFailed(
            "pipeline target node was not found",
            details={"reason": "target_node_not_found", "targetNodeIds": missing},
        )
    return targets


def _selected_node_ids(graph: PipelineGraphV2, targets: tuple[str, ...]) -> frozenset[str]:
    if not targets:
        return frozenset(node["id"] for node in graph["nodes"])
    incoming: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        incoming.setdefault(edge["targetNodeId"], []).append(edge["sourceNodeId"])
    selected = set(targets)
    pending = list(targets)
    while pending:
        node_id = pending.pop()
        for source_id in incoming.get(node_id, []):
            if source_id not in selected:
                selected.add(source_id)
                pending.append(source_id)
    return frozenset(selected)


def _topological_node_ids(
    selected: frozenset[str],
    edges: Sequence[PipelineV2Edge],
) -> tuple[str, ...]:
    incoming = {node_id: 0 for node_id in selected}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in selected}
    for edge in edges:
        source = edge["sourceNodeId"]
        target = edge["targetNodeId"]
        if source in selected and target in selected:
            incoming[target] += 1
            outgoing[source].append(target)
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    ordered = _consume_topology(ready, incoming, outgoing)
    if len(ordered) != len(selected):
        raise PipelinePlanCompilationFailed("pipeline selected path contains a cycle")
    return tuple(ordered)


def _consume_topology(
    ready: list[str],
    incoming: dict[str, int],
    outgoing: Mapping[str, Sequence[str]],
) -> list[str]:
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    return ordered


def _required_descriptor(node: PipelineV2Node) -> PipelineNodeDescriptor:
    descriptor = pipeline_node_descriptor(node["descriptorId"], node["specVersion"])
    if descriptor is None:
        raise PipelinePlanCompilationFailed(
            "pipeline node descriptor is not registered",
            details={
                "nodeId": node["id"],
                "descriptorId": node["descriptorId"],
                "specVersion": node["specVersion"],
            },
        )
    return descriptor


def _plan_node(
    node: PipelineV2Node, descriptor: PipelineNodeDescriptor, output_contract: Mapping[str, object]
) -> PipelinePlanNode:
    config = source_config_without_untrusted_schema(descriptor.descriptor_id, node["config"])
    if descriptor.descriptor_id == "output.dataset":
        config["outputContract"] = dict(output_contract)
    return PipelinePlanNode(
        node_id=node["id"],
        node_kind=PipelineNodeKind(node["kind"]),
        descriptor_id=descriptor.descriptor_id,
        spec_version=descriptor.spec_version,
        runtime_capability=descriptor.runtime_capability,
        config=config,
        execution_policy={
            "maximumAttempts": descriptor.execution_policy.maximum_attempts,
            "initialBackoffSeconds": descriptor.execution_policy.initial_backoff_seconds,
            "maximumBackoffSeconds": descriptor.execution_policy.maximum_backoff_seconds,
            "timeoutSeconds": descriptor.execution_policy.timeout_seconds,
            "requiresStableIdempotency": descriptor.execution_policy.requires_stable_idempotency,
        },
    )


def _selected_source_contracts(
    nodes: Sequence[PipelinePlanNode],
    source_resolution: PipelineSourceResolution | None,
    *,
    require_source_contracts: bool,
) -> tuple[PipelineSourceContract, ...]:
    contracts = source_resolution.contracts if source_resolution is not None else ()
    by_node = source_contracts_by_node(contracts)
    selected: list[PipelineSourceContract] = []
    for node in nodes:
        if node.descriptor_id not in _CONTRACT_BEARING_SOURCES:
            continue
        contract = by_node.get(node.node_id)
        if contract is None and require_source_contracts:
            raise PipelinePlanCompilationFailed(
                "pipeline source contract is required for an executable plan",
                details={"reason": "source_contract_missing", "nodeId": node.node_id},
            )
        if contract is not None:
            _require_matching_source_contract(node, contract)
            selected.append(contract)
    return tuple(selected)


def _require_matching_source_contract(
    node: PipelinePlanNode,
    contract: PipelineSourceContract,
) -> None:
    if contract.descriptor_id == node.descriptor_id:
        return
    raise PipelinePlanCompilationFailed(
        "pipeline source contract descriptor does not match the graph node",
        details={
            "reason": "source_contract_descriptor_mismatch",
            "nodeId": node.node_id,
            "expectedDescriptorId": node.descriptor_id,
            "actualDescriptorId": contract.descriptor_id,
        },
    )


def _plan_edges(
    edges: Sequence[PipelineV2Edge],
    selected: frozenset[str],
) -> tuple[PipelinePlanEdge, ...]:
    planned = [
        PipelinePlanEdge(
            edge_id=edge["id"],
            source_node_id=edge["sourceNodeId"],
            source_port_id=edge["sourcePortId"],
            target_node_id=edge["targetNodeId"],
            target_port_id=edge["targetPortId"],
        )
        for edge in edges
        if edge["sourceNodeId"] in selected and edge["targetNodeId"] in selected
    ]
    return tuple(planned)


def _plan_artifacts(nodes: Sequence[PipelinePlanNode]) -> tuple[PipelineArtifactRef, ...]:
    artifacts: list[PipelineArtifactRef] = []
    for node in nodes:
        descriptor = _descriptor_for_plan_node(node)
        for port in descriptor.output_ports:
            artifacts.append(
                PipelineArtifactRef(
                    artifact_id=_artifact_id(node.node_id, port.port_id),
                    artifact_kind=port.artifact_kind,
                    producer_node_id=node.node_id,
                    producer_port_id=port.port_id,
                    descriptor_id=node.descriptor_id,
                    spec_version=node.spec_version,
                    resource_ref=_resource_ref(node.config),
                )
            )
    return tuple(artifacts)


def _descriptor_for_plan_node(node: PipelinePlanNode) -> PipelineNodeDescriptor:
    descriptor = pipeline_node_descriptor(node.descriptor_id, node.spec_version)
    if descriptor is None:
        raise PipelinePlanCompilationFailed(
            "pinned pipeline descriptor disappeared",
            details={"descriptorId": node.descriptor_id, "specVersion": node.spec_version},
        )
    return descriptor


def _result_artifact_ids(
    nodes: Sequence[PipelinePlanNode],
    artifacts: Sequence[PipelineArtifactRef],
    targets: tuple[str, ...],
) -> tuple[str, ...]:
    result_nodes = set(targets)
    if not result_nodes:
        result_nodes = {node.node_id for node in nodes if node.node_kind == PipelineNodeKind.OUTPUT}
    return tuple(artifact.artifact_id for artifact in artifacts if artifact.producer_node_id in result_nodes)


def _resource_ref(config: Mapping[str, object]) -> str | None:
    for key in (
        "outputDatasetRef",
        "datasetRef",
        "mediaSetRef",
        "virtualTableRef",
        "indexRef",
        "mappingRef",
        "resourceRef",
        "sourceRef",
    ):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _artifact_id(node_id: str, port_id: str) -> str:
    return f"artifact:{node_id}:{port_id}"


def _runtime_failure_details(
    node: PipelineV2Node,
    descriptor: PipelineNodeDescriptor,
) -> JsonObject:
    return {
        "nodeId": node["id"],
        "descriptorId": descriptor.descriptor_id,
        "specVersion": descriptor.spec_version,
        "availability": descriptor.availability.value,
        "runtimeCapability": descriptor.runtime_capability,
    }


def _relevant_warnings(
    warnings: Sequence[JsonObject],
    selected: frozenset[str],
) -> tuple[Mapping[str, object], ...]:
    relevant: list[Mapping[str, object]] = []
    for warning in warnings:
        node_id = warning.get("nodeId")
        if not isinstance(node_id, str) or node_id in selected:
            relevant.append(warning)
    return tuple(relevant)


def _sorted_model_refs(model_refs: Sequence[ModelRef]) -> tuple[ModelRef, ...]:
    return tuple(
        sorted(
            model_refs,
            key=lambda model: (
                model.model_id,
                model.model_version,
                model.provider,
                model.revision,
            ),
        )
    )


def _json_rows(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [{str(key): item_value for key, item_value in item.items()} for item in value if isinstance(item, Mapping)]


def _required_text(value: str, field: str) -> str:
    if not value.strip():
        raise PipelinePlanCompilationFailed(f"{field} is required")
    return value.strip()
