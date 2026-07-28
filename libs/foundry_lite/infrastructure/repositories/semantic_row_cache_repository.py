"""SQLAlchemy persistence for successful Pipeline semantic row cache entries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.semantic_row_cache_repository import (
    SemanticRowCacheRecord,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemySemanticRowCacheRepository:
    """Tenant-scoped immutable cache rows with concurrent insert convergence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def row_by_key(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        cache_key: str,
    ) -> SemanticRowCacheRecord | None:
        row = (
            transaction.execute(
                select(db.pipeline_semantic_row_cache).where(
                    and_(
                        db.pipeline_semantic_row_cache.c.tenant_id == tenant_id,
                        db.pipeline_semantic_row_cache.c.cache_key == cache_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _record_from_row(row) if row else None

    def insert_success(
        self,
        *,
        transaction: Any,
        record: SemanticRowCacheRecord,
    ) -> SemanticRowCacheRecord:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.pipeline_semantic_row_cache).values(
                    id=record.semantic_row_cache_id,
                    tenant_id=record.tenant_id,
                    cache_key=record.cache_key,
                    request_fingerprint=record.request_fingerprint,
                    pipeline_id=record.pipeline_id,
                    scope_kind=record.scope_kind,
                    scope_id=record.scope_id,
                    node_id=record.node_id,
                    descriptor_id=record.descriptor_id,
                    spec_version=record.spec_version,
                    cache_generation=record.cache_generation,
                    resource_security_policy_fingerprint=record.resource_security_policy_fingerprint,
                    model_alias=record.model_alias,
                    environment=record.environment,
                    resolved_model_id=record.resolved_model_id,
                    resolved_model_revision=record.resolved_model_revision,
                    provider=record.provider,
                    prompt_version_id=record.prompt_version_id,
                    prompt_fingerprint=record.prompt_fingerprint,
                    input_fingerprint=record.input_fingerprint,
                    media_fingerprint=record.media_fingerprint,
                    output_schema_fingerprint=record.output_schema_fingerprint,
                    config_fingerprint=record.config_fingerprint,
                    output_value=record.output_value,
                    model_evidence=dict(record.model_evidence),
                    created_at=record.created_at,
                )
            )
        except IntegrityError:
            savepoint.rollback()
            stored = self.row_by_key(
                transaction=transaction,
                tenant_id=record.tenant_id,
                cache_key=record.cache_key,
            )
            if stored is None:
                raise
            return stored
        else:
            savepoint.commit()
        return record


def _record_from_row(row: Any) -> SemanticRowCacheRecord:
    return SemanticRowCacheRecord(
        semantic_row_cache_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        cache_key=str(row["cache_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        pipeline_id=str(row["pipeline_id"]),
        scope_kind=str(row["scope_kind"]),
        scope_id=str(row["scope_id"]),
        node_id=str(row["node_id"]),
        descriptor_id=str(row["descriptor_id"]),
        spec_version=str(row["spec_version"]),
        cache_generation=int(row["cache_generation"]),
        resource_security_policy_fingerprint=str(row["resource_security_policy_fingerprint"]),
        model_alias=str(row["model_alias"]),
        environment=str(row["environment"]),
        resolved_model_id=str(row["resolved_model_id"]),
        resolved_model_revision=str(row["resolved_model_revision"]),
        provider=str(row["provider"]),
        prompt_version_id=str(row["prompt_version_id"]),
        prompt_fingerprint=str(row["prompt_fingerprint"]),
        input_fingerprint=str(row["input_fingerprint"]),
        media_fingerprint=str(row["media_fingerprint"]),
        output_schema_fingerprint=str(row["output_schema_fingerprint"]),
        config_fingerprint=str(row["config_fingerprint"]),
        output_value=row["output_value"],
        model_evidence=dict(row["model_evidence"]),
        created_at=str(row["created_at"]),
    )
