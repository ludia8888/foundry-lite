"""Canonical JSON graph row types for Pipeline Builder v2."""

from typing import Literal, NotRequired, TypedDict

JsonObject = dict[str, object]


class PipelineV2Node(TypedDict):
    """Canonical node row in a Pipeline Builder v2 graph."""

    id: str
    kind: Literal["source", "transform", "output"]
    descriptorId: str
    specVersion: int
    config: JsonObject


class PipelineV2Edge(TypedDict):
    """Typed directed edge between v2 node ports."""

    id: str
    sourceNodeId: str
    sourcePortId: str
    targetNodeId: str
    targetPortId: str


class PipelineGraphV2(TypedDict):
    """Canonical persisted Pipeline Builder v2 graph."""

    schemaVersion: Literal[2]
    nodes: list[PipelineV2Node]
    edges: list[PipelineV2Edge]
    layout: JsonObject
    outputContract: JsonObject
    tests: list[JsonObject]
    schedule: object | None
    metadata: NotRequired[JsonObject]
