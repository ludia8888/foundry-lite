"""Transform registration/run and materialization tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

transforms = Table(
    "transforms",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("language", String, nullable=False),
    Column("entrypoint", Text, nullable=False),
    Column("mode", String, nullable=False),
    Column("inputs", JSON, nullable=False),
    Column("output_dataset_ref", String, nullable=False),
    Column("checks", JSON, nullable=False),
    UniqueConstraint("tenant_id", "api_name", name="uq_transform_api_name"),
)


transform_runs = Table(
    "transform_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("transform_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("input_versions", JSON, nullable=False),
    Column("definition_snapshot", JSON),
    Column("output_version_id", String),
    Column("transaction_id", String),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
    # Set once when a FAILED run's retry is claimed, so a second retry of the
    # same failed run cannot open another output-producing transaction.
    Column("retried_at", String),
)


materializations = Table(
    "materializations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("materialization_type", String, nullable=False),
    Column("source_ref", JSON, nullable=False),
    Column("target_ref", JSON, nullable=False),
    Column("trigger_config", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    UniqueConstraint("tenant_id", "api_name", name="uq_materialization_api_name"),
)


materialization_runs = Table(
    "materialization_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("materialization_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("source_cursor", JSON),
    Column("object_store_watermark", JSON),
    Column("consistency_level", String, nullable=False),
    Column("target_dataset_version_id", String),
    Column("row_count", Integer),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
)
