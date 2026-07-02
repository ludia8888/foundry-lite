"""Tenant, user, and audit event tables."""

from __future__ import annotations

from sqlalchemy import JSON, Column, String, Table

from foundry_lite.infrastructure.schema.base import metadata

tenants = Table(
    "tenants",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("created_at", String, nullable=False),
)


users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("email", String, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)


audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("actor_user_id", String),
    Column("event_type", String, nullable=False),
    Column("resource_type", String, nullable=False),
    Column("resource_id", String),
    Column("action", String),
    Column("decision", String),
    Column("policy_decision", JSON),
    Column("before_ref", JSON),
    Column("after_ref", JSON),
    Column("correlation_id", String),
    Column("request_id", String),
    Column("metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)
