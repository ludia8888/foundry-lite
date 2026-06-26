"""Contract for ``ModelRegistryRepository`` (AIP-lite P0b model catalog + alias indirection, §10.1).

Runs against SQLite (fast inner loop) and PostgreSQL (production parity). Locks the batch read
shape (``get_aliases``/``get_models``/``get_providers`` accept lists, are tenant-scoped) and the
alias→model→provider round-trip including EVERY canonical §10.1 column (capabilities_json/
pricing_json/eval_run_id/effective_at/retired_at/environment/status + the OPTIONAL pinned
``version`` — None → floating latest). The Postgres run also proves the JSON columns round-trip.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from foundry_lite.application.ports.model_registry_repository import (
    ModelAliasRecord,
    ModelProviderRecord,
    ModelRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyModelRegistryRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from tests.contracts.conftest import PostgresFixture, skip_if_no_postgres

_TENANT = "tenant-demo"


@contextmanager
def _txn(engine: Engine) -> Iterator[Any]:
    with engine.begin() as conn:
        yield conn


def _seed(repository: SqlAlchemyModelRegistryRepository, transaction: Any, *, version: str | None) -> None:
    repository.create_provider(
        transaction=transaction,
        record=ModelProviderRecord(
            provider_id="prov-1",
            tenant_scope=_TENANT,
            provider_type="acme",
            profile_name="acme-proxy",
            region="us-east-1",
            secret_ref="foundry_aip_token",
            retention_policy="zero_retention",
            training_policy="no_train",
            status="active",
            created_at="2026-06-25T00:00:00Z",
        ),
    )
    repository.create_model(
        transaction=transaction,
        record=ModelRecord(
            model_id="model-1",
            tenant_scope=_TENANT,
            provider_id="prov-1",
            provider_model_id="acme-large-v2",
            revision="2026-06-01",
            lifecycle="stable",
            capabilities_json={"streaming": True, "native_tools": True},
            context_limit=8192,
            output_limit=1024,
            pricing_json={"input_per_1k": 0.5, "currency": "USD"},
            allowed_classifications=("public", "internal"),
            created_at="2026-06-25T00:00:00Z",
        ),
    )
    repository.create_alias(
        transaction=transaction,
        record=ModelAliasRecord(
            alias="default-completion",
            tenant_scope=_TENANT,
            environment="prod",
            model_id="model-1",
            version=version,
            status="enabled",
            eval_run_id="eval-run-7",
            effective_at="2026-06-25T00:00:00Z",
            retired_at=None,
        ),
    )


def _assert_round_trip(engine: Engine, *, version: str | None) -> None:
    repository = SqlAlchemyModelRegistryRepository(engine)
    with _txn(engine) as transaction:
        _seed(repository, transaction, version=version)
        aliases = repository.get_aliases(transaction=transaction, tenant_id=_TENANT, aliases=["default-completion"])
        models = repository.get_models(transaction=transaction, tenant_id=_TENANT, model_ids=["model-1"])
        providers = repository.get_providers(transaction=transaction, tenant_id=_TENANT, provider_ids=["prov-1"])
        empty = repository.get_models(transaction=transaction, tenant_id=_TENANT, model_ids=[])
        other_tenant = repository.get_aliases(
            transaction=transaction, tenant_id="tenant-test", aliases=["default-completion"]
        )

    alias = aliases[0]
    assert alias.version == version
    assert alias.model_id == "model-1"
    assert alias.environment == "prod"
    assert alias.status == "enabled"
    assert alias.eval_run_id == "eval-run-7"
    assert alias.effective_at == "2026-06-25T00:00:00Z"
    assert alias.retired_at is None

    model = models[0]
    assert model.provider_model_id == "acme-large-v2"
    assert model.revision == "2026-06-01"
    assert model.lifecycle == "stable"
    assert model.capabilities_json == {"streaming": True, "native_tools": True}
    assert model.context_limit == 8192
    assert model.output_limit == 1024
    assert model.pricing_json == {"input_per_1k": 0.5, "currency": "USD"}
    assert model.allowed_classifications == ("public", "internal")
    assert model.created_at == "2026-06-25T00:00:00Z"

    provider = providers[0]
    assert provider.provider_type == "acme"
    assert provider.profile_name == "acme-proxy"
    assert provider.region == "us-east-1"
    assert provider.secret_ref == "foundry_aip_token"
    assert provider.retention_policy == "zero_retention"
    assert provider.training_policy == "no_train"
    assert provider.status == "active"
    assert provider.created_at == "2026-06-25T00:00:00Z"

    assert empty == []
    assert other_tenant == []  # tenant-scoped: another tenant never sees this alias


def _sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine


def test_model_registry_pins_revision_round_trip_sqlite() -> None:
    _assert_round_trip(_sqlite_engine(), version="2026-05-15")


def test_model_registry_floating_latest_round_trip_sqlite() -> None:
    _assert_round_trip(_sqlite_engine(), version=None)


@skip_if_no_postgres
def test_model_registry_round_trip_postgres(postgres_fixture: PostgresFixture) -> None:
    _assert_round_trip(postgres_fixture.engine, version="2026-05-15")
