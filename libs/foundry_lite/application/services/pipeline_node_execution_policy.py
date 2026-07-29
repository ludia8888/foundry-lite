"""Descriptor-owned retry and timeout policy for Pipeline nodes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineNodeExecutionPolicy:
    """Retry, backoff, timeout, and idempotency policy pinned in a plan."""

    maximum_attempts: int = 3
    initial_backoff_seconds: int = 1
    maximum_backoff_seconds: int = 30
    timeout_seconds: int = 300
    requires_stable_idempotency: bool = False


def pipeline_node_execution_policy(descriptor_id: str) -> PipelineNodeExecutionPolicy:
    """Return the catalog-owned execution policy for one descriptor."""

    if descriptor_id in {"transform.use_llm", "transform.trained_model"}:
        return PipelineNodeExecutionPolicy(
            maximum_attempts=1,
            requires_stable_idempotency=True,
        )
    return PipelineNodeExecutionPolicy()
