"""Model-catalog bootstrap records used by the application facade."""

from __future__ import annotations

from foundry_lite.application.ports.model_registry_repository import (
    ModelAliasRecord,
    ModelCatalogSeed,
    ModelProviderRecord,
    ModelRecord,
    ModelRegistryRepository,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.domain.context import DEFAULT_TENANT_ID

_LOCAL_FAKE_AUTH_REFERENCE = "local-fake-reference"


def ensure_model_catalog(
    repository: ModelRegistryRepository,
    transaction: TransactionContext,
    seed: ModelCatalogSeed | None,
    now: str,
) -> None:
    selected = seed or default_model_catalog_seed()
    _ensure_provider(repository, transaction, selected, now)
    _ensure_model(repository, transaction, selected, now)
    _ensure_aliases(repository, transaction, selected, now)


def _ensure_provider(
    repository: ModelRegistryRepository,
    transaction: TransactionContext,
    seed: ModelCatalogSeed,
    now: str,
) -> None:
    existing = repository.get_providers(
        transaction=transaction,
        tenant_id=DEFAULT_TENANT_ID,
        provider_ids=[seed.provider_id],
    )
    if not existing:
        repository.create_provider(transaction=transaction, record=provider_record(seed, now))


def _ensure_model(
    repository: ModelRegistryRepository,
    transaction: TransactionContext,
    seed: ModelCatalogSeed,
    now: str,
) -> None:
    existing = repository.get_models(
        transaction=transaction,
        tenant_id=DEFAULT_TENANT_ID,
        model_ids=[seed.model_id],
    )
    if not existing:
        repository.create_model(transaction=transaction, record=model_record(seed, now))


def _ensure_aliases(
    repository: ModelRegistryRepository,
    transaction: TransactionContext,
    seed: ModelCatalogSeed,
    now: str,
) -> None:
    existing = repository.get_aliases(
        transaction=transaction,
        tenant_id=DEFAULT_TENANT_ID,
        aliases=list(seed.aliases),
    )
    for alias in sorted(set(seed.aliases) - {row.alias for row in existing}):
        repository.create_alias(transaction=transaction, record=alias_record(seed, alias, now))


def provider_record(seed: ModelCatalogSeed, now: str) -> ModelProviderRecord:
    return ModelProviderRecord(
        provider_id=seed.provider_id,
        tenant_scope=DEFAULT_TENANT_ID,
        provider_type=seed.provider_type,
        profile_name=seed.profile_name,
        region=seed.region,
        secret_ref=seed.secret_ref,
        retention_policy=seed.retention_policy,
        training_policy=seed.training_policy,
        status="active",
        created_at=now,
    )


def model_record(seed: ModelCatalogSeed, now: str) -> ModelRecord:
    return ModelRecord(
        model_id=seed.model_id,
        tenant_scope=DEFAULT_TENANT_ID,
        provider_id=seed.provider_id,
        provider_model_id=seed.provider_model_id,
        revision=seed.revision,
        lifecycle=seed.lifecycle,
        capabilities_json=dict(seed.capabilities_json),
        context_limit=seed.context_limit,
        output_limit=seed.output_limit,
        pricing_json=dict(seed.pricing_json),
        allowed_classifications=seed.allowed_classifications,
        created_at=now,
    )


def alias_record(seed: ModelCatalogSeed, alias: str, now: str) -> ModelAliasRecord:
    return ModelAliasRecord(
        alias=alias,
        tenant_scope=DEFAULT_TENANT_ID,
        environment="prod",
        model_id=seed.model_id,
        version=seed.revision,
        status="enabled",
        eval_run_id=None,
        effective_at=now,
        retired_at=None,
    )


def default_model_catalog_seed() -> ModelCatalogSeed:
    return ModelCatalogSeed(
        provider_id="local-fake-provider",
        provider_type="local-fake",
        profile_name="fake-language-model",
        region="us-east-1",
        secret_ref=_LOCAL_FAKE_AUTH_REFERENCE,
        retention_policy="zero_retention",
        training_policy="no_train",
        model_id="local-fake-model",
        provider_model_id="local-fake-echo",
        revision="2026-06-25",
        lifecycle="stable",
        capabilities_json={
            "streaming": True,
            "native_tools": False,
            "image_input": True,
            "pdf_input": True,
            "structured_outputs": True,
        },
        context_limit=8192,
        output_limit=1024,
        pricing_json={"input_per_1k": 0.002, "output_per_1k": 0.006, "currency": "USD"},
        allowed_classifications=("public", "internal"),
        aliases=("default-completion", "gpt-governed", "document-vlm"),
    )
