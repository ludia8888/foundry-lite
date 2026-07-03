"""Ontology version and object/property/link/action type tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

ontology_versions = Table(
    "ontology_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("created_by", String),
    Column("created_at", String, nullable=False),
    Column("activated_at", String),
)
Index(
    "uq_ontology_active_per_tenant",
    ontology_versions.c.tenant_id,
    unique=True,
    sqlite_where=ontology_versions.c.status == "active",
    postgresql_where=ontology_versions.c.status == "active",
)


object_types = Table(
    "object_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("description", Text),
    Column("primary_key_property", String, nullable=False),
    Column("backing", JSON, nullable=False),
    Column("config", JSON, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_object_type_api"),
)


property_types = Table(
    "property_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("data_type", String, nullable=False),
    Column("nullable", Boolean, nullable=False),
    Column("indexed", Boolean, nullable=False),
    Column("searchable", Boolean, nullable=False),
    Column("editable", Boolean, nullable=False),
    Column("classification", String),
    Column("source", String, nullable=False),
    Column("column_name", String),
    Column("edit_policy", String, nullable=False),
    Column("derivation", JSON),
    UniqueConstraint("object_type_id", "api_name", name="uq_property_api"),
)


link_types = Table(
    "link_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("from_object_type_id", String, nullable=False),
    Column("from_api_name", String, nullable=False),
    Column("to_object_type_id", String, nullable=False),
    Column("to_api_name", String, nullable=False),
    Column("cardinality", String, nullable=False),
    Column("backing", JSON, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_link_type_api"),
)


action_types = Table(
    "action_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("target_object_type_id", String, nullable=False),
    Column("target_api_name", String, nullable=False),
    Column("parameter_schema", JSON, nullable=False),
    Column("definition", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_action_type_api"),
)


interface_types = Table(
    "interface_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("definition", JSON, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_interface_type_api"),
)


function_types = Table(
    "function_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("definition", JSON, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_function_type_api"),
)
