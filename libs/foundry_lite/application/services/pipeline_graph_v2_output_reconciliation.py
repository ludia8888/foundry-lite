"""Typed error evidence for Pipeline Graph v2 serving-output reconciliation."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
)

JsonObject = dict[str, object]


def is_reconcilable_committed_output(
    node: PipelineV2RuntimeNode,
    artifact: PipelineV2RuntimeArtifact,
) -> bool:
    return node.kind == "output" and artifact.status == "COMMITTED" and artifact.is_serving


def committed_output_evidence_error(
    node: PipelineV2RuntimeNode,
    cause: Mapping[str, object],
) -> JsonObject:
    return {
        "type": "PIPELINE_OUTPUT_EVIDENCE_PERSISTENCE_FAILED",
        "message": "serving output committed but its pipeline artifact passport requires reconciliation",
        "details": _committed_details(node, cause),
    }


def committed_output_post_commit_error(
    node: PipelineV2RuntimeNode,
    cause: Mapping[str, object],
) -> JsonObject:
    return {
        "type": "PIPELINE_OUTPUT_POST_COMMIT_VALIDATION_FAILED",
        "message": "serving output committed but post-commit validation requires reconciliation",
        "details": _committed_details(node, cause),
    }


def commit_outcome_unknown_error(
    node: PipelineV2RuntimeNode,
    cause: Mapping[str, object],
) -> JsonObject:
    return {
        "type": "PIPELINE_OUTPUT_COMMIT_OUTCOME_UNKNOWN",
        "message": "serving output commit outcome is unknown and requires transaction reconciliation",
        "details": {
            "nodeId": node.node_id,
            "descriptorId": node.descriptor_id,
            "servingCommitState": "UNKNOWN",
            "transactionReconciliationRequired": True,
            "cause": dict(cause),
        },
    }


def _committed_details(
    node: PipelineV2RuntimeNode,
    cause: Mapping[str, object],
) -> JsonObject:
    return {
        "nodeId": node.node_id,
        "descriptorId": node.descriptor_id,
        "servingCommitPreserved": True,
        "cause": dict(cause),
    }
