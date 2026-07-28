"""Typed failures emitted by isolated Pipeline preview strategies."""

from foundry_lite.domain.errors import ValidationFailed


class PipelineCodeIsolationRequired(ValidationFailed):
    """Unsaved user code cannot run inside the API or preview process."""

    code = "PIPELINE_CODE_ISOLATION_REQUIRED"
