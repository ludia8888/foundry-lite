"""Action run and writeback tables."""

from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, String, Table, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

action_runs = Table(
    "action_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_type_id", String, nullable=False),
    Column("action_type_api_name", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("target_object_type_id", String, nullable=False),
    Column("target_object_type_api_name", String, nullable=False),
    Column("target_object_id", String, nullable=False),
    Column("expected_object_version", Integer, nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("result", JSON),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
    UniqueConstraint(
        "tenant_id",
        "action_type_id",
        "actor_user_id",
        "idempotency_key",
        name="uq_action_idempotency",
    ),
)


action_writebacks = Table(
    "action_writebacks",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_run_id", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("connector_id", String),
    Column("request", JSON, nullable=False),
    Column("response", JSON),
    Column("status", String, nullable=False),
    Column("idempotency_key", String),
    Column("attempts", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
)
