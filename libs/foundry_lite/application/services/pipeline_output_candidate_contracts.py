"""Typed contracts for governed non-serving Pipeline output candidates."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class PipelineOutputCandidatePolicy:
    legacy_node_type: str
    descriptor_id: str
    artifact_kind: PipelineArtifactKind
    plane: str
    port_id: str
    resource_field: str
    candidate_type: str
    accepted_input_kinds: tuple[PipelineArtifactKind, ...]
    validation_contract: str
    serving_gap: str


SEMANTIC_INDEX_CANDIDATE = PipelineOutputCandidatePolicy(
    legacy_node_type="output_semantic_index_candidate",
    descriptor_id="output.semantic_index",
    artifact_kind=PipelineArtifactKind.VECTOR_INDEX_GENERATION,
    plane="index",
    port_id="index",
    resource_field="indexRef",
    candidate_type="semantic_index_generation",
    accepted_input_kinds=(
        PipelineArtifactKind.VECTOR_INDEX_GENERATION,
        PipelineArtifactKind.DATASET_VERSION,
        PipelineArtifactKind.CONTENT_UNIT_SET,
    ),
    validation_contract="semantic-index-candidate-v1",
    serving_gap="index generation build, validation, and active-generation promotion remain a governed follow-up",
)

ONTOLOGY_MAPPING_CANDIDATE = PipelineOutputCandidatePolicy(
    legacy_node_type="output_ontology_mapping_candidate",
    descriptor_id="output.ontology",
    artifact_kind=PipelineArtifactKind.ONTOLOGY_MAPPING,
    plane="ontology",
    port_id="mapping",
    resource_field="mappingRef",
    candidate_type="ontology_mapping",
    accepted_input_kinds=(
        PipelineArtifactKind.DATASET_VERSION,
        PipelineArtifactKind.CONTENT_UNIT_SET,
        PipelineArtifactKind.MEDIA_SET_SELECTION,
    ),
    validation_contract="ontology-mapping-candidate-v1",
    serving_gap="Ontology proposal review, activation, and object reindex remain a governed follow-up",
)

PIPELINE_OUTPUT_CANDIDATE_POLICIES = (
    SEMANTIC_INDEX_CANDIDATE,
    ONTOLOGY_MAPPING_CANDIDATE,
)
_POLICY_BY_NODE_TYPE = {policy.legacy_node_type: policy for policy in PIPELINE_OUTPUT_CANDIDATE_POLICIES}
_POLICY_BY_DESCRIPTOR = {policy.descriptor_id: policy for policy in PIPELINE_OUTPUT_CANDIDATE_POLICIES}


def candidate_policy_for_node_type(node_type: str) -> PipelineOutputCandidatePolicy | None:
    return _POLICY_BY_NODE_TYPE.get(node_type)


def require_candidate_policy(descriptor_id: str) -> PipelineOutputCandidatePolicy:
    policy = _POLICY_BY_DESCRIPTOR.get(descriptor_id)
    if policy is None:
        raise ValidationFailed(
            "pipeline governed output candidate descriptor is unsupported",
            details={"descriptorId": descriptor_id},
        )
    return policy
