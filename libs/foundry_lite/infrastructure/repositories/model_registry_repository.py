"""SQLAlchemy repository adapter for model registry repository persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select

from foundry_lite.application.ports.model_registry_repository import (
    AliasStatus,
    ModelAliasRecord,
    ModelLifecycle,
    ModelProviderRecord,
    ModelRecord,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyModelRegistryRepository:
    """SQLAlchemy implementation of the AIP model catalog, providers, and alias indirection."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create_provider(self, *, transaction: Any, record: ModelProviderRecord) -> None:
        transaction.execute(
            insert(db.ai_model_providers).values(
                id=record.provider_id,
                tenant_id=record.tenant_scope,
                tenant_scope=record.tenant_scope,
                provider_type=record.provider_type,
                profile_name=record.profile_name,
                region=record.region,
                secret_ref=record.secret_ref,
                retention_policy=record.retention_policy,
                training_policy=record.training_policy,
                status=record.status,
                created_at=record.created_at,
            )
        )

    def create_model(self, *, transaction: Any, record: ModelRecord) -> None:
        transaction.execute(
            insert(db.ai_models).values(
                id=record.model_id,
                tenant_id=record.tenant_scope,
                tenant_scope=record.tenant_scope,
                provider_id=record.provider_id,
                provider_model_id=record.provider_model_id,
                revision=record.revision,
                lifecycle=record.lifecycle,
                capabilities_json=dict(record.capabilities_json),
                context_limit=record.context_limit,
                output_limit=record.output_limit,
                pricing_json=dict(record.pricing_json),
                allowed_classifications=list(record.allowed_classifications),
                created_at=record.created_at,
            )
        )

    def create_alias(self, *, transaction: Any, record: ModelAliasRecord) -> None:
        transaction.execute(
            insert(db.ai_model_aliases).values(
                id=f"{record.tenant_scope}:{record.environment}:{record.alias}",
                tenant_id=record.tenant_scope,
                tenant_scope=record.tenant_scope,
                alias=record.alias,
                environment=record.environment,
                model_id=record.model_id,
                version=record.version,
                status=record.status,
                eval_run_id=record.eval_run_id,
                effective_at=record.effective_at,
                retired_at=record.retired_at,
            )
        )

    def get_aliases(self, *, transaction: Any, tenant_id: str, aliases: list[str]) -> list[ModelAliasRecord]:
        if not aliases:
            return []
        rows = (
            transaction.execute(
                select(db.ai_model_aliases).where(
                    and_(
                        db.ai_model_aliases.c.tenant_id == tenant_id,
                        db.ai_model_aliases.c.alias.in_(aliases),
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_alias_from_row(row) for row in rows]

    def get_models(self, *, transaction: Any, tenant_id: str, model_ids: list[str]) -> list[ModelRecord]:
        if not model_ids:
            return []
        rows = (
            transaction.execute(
                select(db.ai_models).where(
                    and_(db.ai_models.c.tenant_id == tenant_id, db.ai_models.c.id.in_(model_ids))
                )
            )
            .mappings()
            .all()
        )
        return [_model_from_row(row) for row in rows]

    def get_providers(self, *, transaction: Any, tenant_id: str, provider_ids: list[str]) -> list[ModelProviderRecord]:
        if not provider_ids:
            return []
        rows = (
            transaction.execute(
                select(db.ai_model_providers).where(
                    and_(
                        db.ai_model_providers.c.tenant_id == tenant_id,
                        db.ai_model_providers.c.id.in_(provider_ids),
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_provider_from_row(row) for row in rows]


def _alias_from_row(row: Any) -> ModelAliasRecord:
    return ModelAliasRecord(
        alias=cast(str, row["alias"]),
        tenant_scope=cast(str, row["tenant_scope"]),
        environment=cast(str, row["environment"]),
        model_id=cast(str, row["model_id"]),
        version=cast("str | None", row["version"]),
        status=cast(AliasStatus, row["status"]),
        eval_run_id=cast("str | None", row["eval_run_id"]),
        effective_at=cast("str | None", row["effective_at"]),
        retired_at=cast("str | None", row["retired_at"]),
    )


def _model_from_row(row: Any) -> ModelRecord:
    return ModelRecord(
        model_id=cast(str, row["id"]),
        tenant_scope=cast(str, row["tenant_scope"]),
        provider_id=cast(str, row["provider_id"]),
        provider_model_id=cast(str, row["provider_model_id"]),
        revision=cast(str, row["revision"]),
        lifecycle=cast(ModelLifecycle, row["lifecycle"]),
        capabilities_json=cast("dict[str, object]", dict(row["capabilities_json"])),
        context_limit=cast(int, row["context_limit"]),
        output_limit=cast(int, row["output_limit"]),
        pricing_json=cast("dict[str, object]", dict(row["pricing_json"])),
        allowed_classifications=tuple(row["allowed_classifications"]),
        created_at=cast(str, row["created_at"]),
    )


def _provider_from_row(row: Any) -> ModelProviderRecord:
    return ModelProviderRecord(
        provider_id=cast(str, row["id"]),
        tenant_scope=cast(str, row["tenant_scope"]),
        provider_type=cast(str, row["provider_type"]),
        profile_name=cast(str, row["profile_name"]),
        region=cast(str, row["region"]),
        secret_ref=cast(str, row["secret_ref"]),
        retention_policy=cast(str, row["retention_policy"]),
        training_policy=cast(str, row["training_policy"]),
        status=cast(str, row["status"]),
        created_at=cast(str, row["created_at"]),
    )
