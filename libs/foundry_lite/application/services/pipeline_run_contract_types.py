"""Repository, state, context, and error contracts for Pipeline runs."""

from foundry_lite.application.ports import (
    DatasetRepository,
    DatasetVersionRepository,
    TransactionContext,
)
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_CANCELLED,
    PIPELINE_RUN_EXECUTING,
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_SUCCEEDED,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, NotFound

__all__ = [
    "PIPELINE_RUN_CANCELLED",
    "PIPELINE_RUN_EXECUTING",
    "PIPELINE_RUN_FAILED",
    "PIPELINE_RUN_SUCCEEDED",
    "ConflictDetected",
    "DatasetRepository",
    "DatasetVersionRepository",
    "InvariantViolation",
    "NotFound",
    "PipelineRepository",
    "PipelineRunRow",
    "PipelineVersionRow",
    "RequestContext",
    "TransactionContext",
]
