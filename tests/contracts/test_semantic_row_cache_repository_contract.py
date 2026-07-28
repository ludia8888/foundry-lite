"""Contract for tenant-scoped immutable Pipeline semantic row cache persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.semantic_row_cache_repository import SemanticRowCacheRecord
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.semantic_row_cache_repository import (
    SqlAlchemySemanticRowCacheRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture(params=["sqlalchemy", "postgres"])
def cache_repo(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> tuple[SqlAlchemySemanticRowCacheRepository, Engine]:
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'semantic-cache.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemySemanticRowCacheRepository(engine), engine
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemySemanticRowCacheRepository(postgres_fixture.engine), postgres_fixture.engine


def test_insert_success_is_immutable_and_idempotent(
    cache_repo: tuple[SqlAlchemySemanticRowCacheRepository, Engine],
) -> None:
    repository, engine = cache_repo
    with engine.begin() as transaction:
        first = repository.insert_success(transaction=transaction, record=_record("cache-a", {"label": "first"}))
        replay = repository.insert_success(transaction=transaction, record=_record("cache-b", {"label": "second"}))

    assert first.semantic_row_cache_id == "cache-a"
    assert replay.semantic_row_cache_id == "cache-a"
    assert replay.output_value == {"label": "first"}
    assert replay.pipeline_id == "pipeline-a"
    assert replay.scope_kind == "branch"
    assert replay.scope_id == "branch-a"
    assert replay.node_id == "semantic-a"
    assert replay.cache_generation == 1
    assert replay.resource_security_policy_fingerprint == "sha256:policy-a"


def test_lookup_is_tenant_scoped_even_for_the_same_cache_key(
    cache_repo: tuple[SqlAlchemySemanticRowCacheRepository, Engine],
) -> None:
    repository, engine = cache_repo
    with engine.begin() as transaction:
        repository.insert_success(transaction=transaction, record=_record("cache-a", {"label": "a"}))
        repository.insert_success(
            transaction=transaction,
            record=_record("cache-b", {"label": "b"}, tenant_id="tenant-b"),
        )
        tenant_a = repository.row_by_key(
            transaction=transaction,
            tenant_id="tenant-a",
            cache_key="exact-key",
        )
        tenant_b = repository.row_by_key(
            transaction=transaction,
            tenant_id="tenant-b",
            cache_key="exact-key",
        )
        hidden = repository.row_by_key(
            transaction=transaction,
            tenant_id="tenant-c",
            cache_key="exact-key",
        )

    assert tenant_a is not None and tenant_a.output_value == {"label": "a"}
    assert tenant_b is not None and tenant_b.output_value == {"label": "b"}
    assert hidden is None


def _record(
    cache_id: str,
    output: object,
    *,
    tenant_id: str = "tenant-a",
) -> SemanticRowCacheRecord:
    return SemanticRowCacheRecord(
        semantic_row_cache_id=cache_id,
        tenant_id=tenant_id,
        cache_key="exact-key",
        request_fingerprint="request-fingerprint",
        pipeline_id="pipeline-a",
        scope_kind="branch",
        scope_id="branch-a",
        node_id="semantic-a",
        descriptor_id="transform.use_llm",
        spec_version="1",
        cache_generation=1,
        resource_security_policy_fingerprint="sha256:policy-a",
        model_alias="semantic-default",
        environment="prod",
        resolved_model_id="semantic-model",
        resolved_model_revision="2026-07-17",
        provider="local-test",
        prompt_version_id="prompt@1",
        prompt_fingerprint="prompt-fingerprint",
        input_fingerprint="input-fingerprint",
        media_fingerprint="media-fingerprint",
        output_schema_fingerprint="schema-fingerprint",
        config_fingerprint="config-fingerprint",
        output_value=output,
        model_evidence={"cacheStatus": "miss"},
        created_at="2026-07-17T00:00:00+00:00",
    )
