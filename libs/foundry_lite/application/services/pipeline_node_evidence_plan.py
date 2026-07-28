"""Read-only execution-plan index used by synchronous Pipeline evidence."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.errors import InvariantViolation

JsonObject = dict[str, object]


class PipelineEvidencePlan:
    """Index named nodes, ports, edges, and pinned model/config coordinates."""

    def __init__(self, execution_plan: Mapping[str, object]) -> None:
        self._plan = dict(execution_plan)

    def node(self, node_id: str) -> JsonObject:
        for node in json_rows(self._plan.get("nodes")):
            if node.get("nodeId") == node_id:
                return node
        raise InvariantViolation("pipeline execution plan node is missing", details={"node_id": node_id})

    def input_artifact_spec(self, node_id: str, dataset_ref: str) -> JsonObject:
        incoming = [edge for edge in json_rows(self._plan.get("edges")) if edge.get("targetNodeId") == node_id]
        specs = [self.artifact_spec(str(edge["sourceNodeId"]), str(edge["sourcePortId"])) for edge in incoming]
        matches = [spec for spec in specs if spec.get("resourceRef") == dataset_ref]
        if len(matches) != 1:
            raise InvariantViolation(
                "pipeline input must resolve through one named incoming edge",
                details={"node_id": node_id, "dataset_ref": dataset_ref, "match_count": len(matches)},
            )
        return matches[0]

    def output_artifact_spec(self, node_id: str) -> JsonObject:
        candidates = [
            spec
            for spec in json_rows(self._plan.get("artifacts"))
            if spec.get("producerNodeId") == node_id and spec.get("artifactKind") == "dataset_version"
        ]
        if len(candidates) != 1:
            raise InvariantViolation(
                "pipeline Dataset node must have one output artifact",
                details={"node_id": node_id, "artifact_count": len(candidates)},
            )
        return candidates[0]

    def artifact_spec(self, node_id: str, port_id: str) -> JsonObject:
        for spec in json_rows(self._plan.get("artifacts")):
            if spec.get("producerNodeId") == node_id and spec.get("producerPortId") == port_id:
                return spec
        raise InvariantViolation(
            "pipeline named output port is missing from execution plan",
            details={"node_id": node_id, "port_id": port_id},
        )

    def incoming_edge(
        self,
        node_id: str,
        source_node_id: str,
        source_port_id: str,
    ) -> JsonObject:
        matches = [
            edge
            for edge in json_rows(self._plan.get("edges"))
            if (
                edge.get("targetNodeId") == node_id
                and edge.get("sourceNodeId") == source_node_id
                and edge.get("sourcePortId") == source_port_id
            )
        ]
        if len(matches) != 1:
            raise InvariantViolation(
                "pipeline named incoming edge is missing or ambiguous",
                details={
                    "node_id": node_id,
                    "source_node_id": source_node_id,
                    "source_port_id": source_port_id,
                    "match_count": len(matches),
                },
            )
        return matches[0]

    def incoming_edges(self, node_id: str) -> tuple[JsonObject, ...]:
        return tuple(edge for edge in json_rows(self._plan.get("edges")) if edge.get("targetNodeId") == node_id)

    def executor_profile(self, node_id: str) -> str:
        capability = self.node(node_id).get("runtimeCapability")
        return str(capability) if isinstance(capability, str) else "tabular_v1_compiler"

    def node_pins(self, node_id: str) -> JsonObject:
        config = self.node(node_id).get("config")
        selected = _selected_config_pins(config if isinstance(config, Mapping) else {})
        return {"config": selected, "modelRefs": json_rows(self._plan.get("modelRefs"))}

    def node_order(self) -> tuple[str, ...]:
        return tuple(str(node["nodeId"]) for node in json_rows(self._plan.get("nodes")))

    def artifact_order(self) -> tuple[str, ...]:
        return tuple(str(artifact["artifactId"]) for artifact in json_rows(self._plan.get("artifacts")))


def json_rows(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _selected_config_pins(config: Mapping[str, object]) -> JsonObject:
    keys = ("modelId", "modelRef", "modelVersion", "promptId", "promptVersion", "processorId", "processorVersion")
    return {key: config[key] for key in keys if key in config}
