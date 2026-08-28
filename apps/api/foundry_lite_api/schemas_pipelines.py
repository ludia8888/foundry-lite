"""Transform, Pipeline, and workflow recovery request schemas."""

from __future__ import annotations

from typing import Literal

from foundry_lite.application.services.pipeline_graph_contracts import (
    DEFAULT_PIPELINE_PREVIEW_ROWS,
    MAX_PIPELINE_PREVIEW_ROWS,
)
from pydantic import BaseModel, ConfigDict, Field

from foundry_lite_api.schema_types import JsonObject


class TransformSqlRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_name: str = Field(alias="apiName")
    sql: str
    inputs: dict[str, str]
    output_dataset_ref: str = Field(alias="outputDatasetRef")
    checks: list[JsonObject] = Field(default_factory=list)
    mode: str = "snapshot"


class TransformSchedulerTickRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_runs: int = Field(default=50, ge=1, le=500, alias="maxRuns")


class PipelineBranchCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pipeline_id: str = Field(alias="pipelineId")
    name: str


class PipelineGraphUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    graph: JsonObject
    expected_fingerprint: str = Field(alias="expectedFingerprint")


class PipelineBranchRebaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_fingerprint: str = Field(alias="expectedFingerprint")


class PipelineBranchProposeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str | None = None


class PipelineProposalAssignRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignee_user_id: str = Field(alias="assigneeUserId")


class PipelineProposalDecisionRequest(BaseModel):
    decision: str
    comment: str | None = None


class PipelinePreviewNodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    limit: int = Field(
        default=DEFAULT_PIPELINE_PREVIEW_ROWS,
        ge=1,
        le=MAX_PIPELINE_PREVIEW_ROWS,
        description="General Pipeline Builder table preview row limit; defaults to and is capped at 500.",
    )


class PipelinePreviewLimitsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table_rows: int = Field(
        default=DEFAULT_PIPELINE_PREVIEW_ROWS,
        ge=1,
        le=MAX_PIPELINE_PREVIEW_ROWS,
        alias="tableRows",
        description=(
            "General table preview row budget. Defaults to and is capped at 500; "
            "Use LLM output preview is server-capped at 50 rows."
        ),
    )
    media_items: int = Field(default=5, ge=1, le=20, alias="mediaItems")
    pdf_pages: int = Field(default=3, ge=1, le=10, alias="pdfPages")
    audio_video_seconds: int = Field(default=60, ge=1, le=60, alias="audioVideoSeconds")
    scene_count: int = Field(default=12, ge=1, le=12, alias="sceneCount")
    search_hits: int = Field(default=10, ge=1, le=10, alias="searchHits")
    total_bytes: int = Field(default=32 * 1024 * 1024, ge=1, le=32 * 1024 * 1024, alias="totalBytes")
    timeout_seconds: int = Field(default=30, ge=1, le=30, alias="timeoutSeconds")


class PipelinePreviewRunCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    graph: JsonObject
    target_node_id: str | None = Field(default=None, alias="targetNodeId")
    limits: PipelinePreviewLimitsRequest = Field(default_factory=PipelinePreviewLimitsRequest)


class PipelineDeployRequest(BaseModel):
    options: JsonObject = Field(default_factory=dict)


class PipelineRunStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: str | None = Field(default=None, alias="versionId")
    parameters: JsonObject = Field(default_factory=dict)
    target_node_ids: list[str] = Field(default_factory=list, alias="targetNodeIds")


class PipelineRunCancelRequest(BaseModel):
    reason: str | None = None


class PipelineScheduleSpecRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trigger_type: Literal["cron", "interval"] = Field(alias="triggerType")
    timezone: str = "UTC"
    cron_expression: str | None = Field(default=None, alias="cronExpression")
    interval_seconds: int | None = Field(default=None, ge=1, alias="intervalSeconds")
    start_at: str | None = Field(default=None, alias="startAt")
    auto_pause_after_failures: int | None = Field(default=None, ge=1, alias="autoPauseAfterFailures")


class PipelineScheduleUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: str = Field(alias="versionId")
    schedule: PipelineScheduleSpecRequest
    enabled: bool = True


class DeadLetterBulkRetryRequest(BaseModel):
    ids: list[str]
