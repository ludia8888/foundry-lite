"""Pipeline Builder graph, governance, run, and schedule tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

pipeline_branches = Table(
    "pipeline_branches",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("base_version_id", String),
    Column("base_graph", JSON, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("rebased_at", String),
    Column("proposal_id", String),
    Column("merged_version_id", String),
)


pipeline_proposals = Table(
    "pipeline_proposals",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("branch_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("status", String, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("assigned_to", String),
    Column("decision", String),
    Column("decision_comment", Text),
    Column("decided_at", String),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)


pipeline_versions = Table(
    "pipeline_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("proposal_id", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("deployed_at", String),
    UniqueConstraint("tenant_id", "pipeline_id", "version_number", name="uq_pipeline_version_number"),
)


pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("output_dataset_ref", String),
    Column("output_version_id", String),
    Column("timeline", JSON, nullable=False),
    Column("error", JSON),
    Column("created_by", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
)


pipeline_schedules = Table(
    "pipeline_schedules",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("schedule", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("updated_by", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("last_tick_at", String),
    UniqueConstraint("tenant_id", "pipeline_id", name="uq_pipeline_schedule_pipeline"),
)


pipeline_test_results = Table(
    "pipeline_test_results",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("branch_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("result", JSON, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
)
