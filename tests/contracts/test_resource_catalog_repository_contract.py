"""Contract for the Compass-style ResourceCatalogRepository."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.services.resource_catalog_records import (
    folder_record,
    grant_record,
    project_record,
    resource_record,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyResourceCatalogRepository
from foundry_lite.infrastructure.repositories import resource_catalog_repository as catalog_repository_module
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from tests.contracts.conftest import PostgresFixture, skip_if_no_postgres

_TENANT = "tenant-demo"
_NOW = "2026-07-05T00:00:00Z"
_RESOURCE_TABLES = (
    "projects",
    "project_grants",
    "project_folders",
    "resources",
    "resource_favorites",
    "resource_idempotency_records",
)


@contextmanager
def _txn(engine: Engine) -> Iterator[Any]:
    with engine.begin() as conn:
        yield conn


def _sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine


def _insert_project(repo: SqlAlchemyResourceCatalogRepository, transaction: Any, ctx: RequestContext) -> dict[str, Any]:
    return repo.insert_project(
        transaction=transaction,
        record=project_record(ctx, display_name="Operations", description=None, metadata={}, now=_NOW),
    )


def _upsert_role_grants(
    repo: SqlAlchemyResourceCatalogRepository,
    transaction: Any,
    ctx: RequestContext,
    project_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    grant = repo.upsert_project_grant(
        transaction=transaction,
        record=grant_record(
            ctx, project_id=project_id, principal_type="role", principal_id="analyst", role="viewer", now=_NOW
        ),
    )
    updated = repo.upsert_project_grant(
        transaction=transaction,
        record=grant_record(
            ctx,
            project_id=project_id,
            principal_type="role",
            principal_id="analyst",
            role="editor",
            now="2026-07-05T00:01:00Z",
        ),
    )
    return grant, updated


def _insert_folder_and_resource(
    repo: SqlAlchemyResourceCatalogRepository,
    transaction: Any,
    ctx: RequestContext,
    project_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    folder = repo.insert_folder(
        transaction=transaction,
        record=folder_record(
            ctx, project_id=project_id, parent_folder_id=None, display_name="Raw", metadata={}, now=_NOW
        ),
    )
    resource = _upsert_dataset_resource(repo, transaction, ctx, project_id, folder["id"], "raw.orders")
    updated = _upsert_dataset_resource(repo, transaction, ctx, project_id, None, "raw.orders.v2")
    return folder, resource, updated


def _upsert_dataset_resource(
    repo: SqlAlchemyResourceCatalogRepository,
    transaction: Any,
    ctx: RequestContext,
    project_id: str,
    folder_id: str | None,
    display_name: str,
) -> dict[str, Any]:
    return repo.upsert_resource(
        transaction=transaction,
        record=resource_record(
            ctx,
            resource_type="dataset",
            display_name=display_name,
            project_id=project_id,
            folder_id=folder_id,
            source_surface="dataset",
            source_ref="raw.orders",
            operations_path="/datasets/raw/orders",
            metadata={"classification": "public"},
            now=_NOW,
        ),
    )


def _write_favorite_and_idempotency(
    repo: SqlAlchemyResourceCatalogRepository,
    transaction: Any,
    resource_id: str,
    rid: str,
) -> None:
    repo.add_favorite(
        transaction=transaction,
        tenant_id=_TENANT,
        resource_id=resource_id,
        actor_user_id="analyst-a",
        now=_NOW,
    )
    repo.store_idempotency_record(
        transaction=transaction,
        tenant_id=_TENANT,
        scope="resources.register",
        idempotency_key="resource-key",
        request_hash="hash-a",
        response_payload={"resource": {"rid": rid}},
        now=_NOW,
    )


def _read_catalog(repo: SqlAlchemyResourceCatalogRepository, transaction: Any, project_id: str) -> dict[str, Any]:
    return {
        "projects": repo.list_projects(transaction=transaction, tenant_id=_TENANT),
        "grants": repo.list_project_grants(transaction=transaction, tenant_id=_TENANT, project_id=project_id),
        "folders": repo.list_folders(
            transaction=transaction, tenant_id=_TENANT, project_id=project_id, include_trashed=False
        ),
        "resources": repo.list_resources(
            transaction=transaction, tenant_id=_TENANT, project_ids=[project_id], folder_id=None, include_trashed=False
        ),
        "favorites": repo.favorite_resource_ids(transaction=transaction, tenant_id=_TENANT, actor_user_id="analyst-a"),
        "idempotency": repo.idempotency_record(
            transaction=transaction, tenant_id=_TENANT, scope="resources.register", idempotency_key="resource-key"
        ),
        "otherTenantResources": repo.list_resources(
            transaction=transaction,
            tenant_id="tenant-test",
            project_ids=[project_id],
            folder_id=None,
            include_trashed=False,
        ),
    }


def _assert_catalog(seed: dict[str, Any], read: dict[str, Any]) -> None:
    assert seed["project"]["rid"].startswith("ri.foundry-lite.project.")
    assert seed["folder"]["rid"].startswith("ri.foundry-lite.folder.")
    assert seed["stableRid"].startswith("ri.foundry-lite.dataset.")
    assert seed["grant"]["role"] == "viewer"
    assert seed["updatedGrant"]["role"] == "editor"
    assert seed["updatedResource"]["rid"] == seed["stableRid"]
    assert seed["updatedResource"]["display_name"] == "raw.orders.v2"
    assert read["projects"] == [seed["project"]]
    assert read["grants"][0]["role"] == "editor"
    assert read["folders"] == [seed["folder"]]
    assert read["resources"][0]["rid"] == seed["stableRid"]
    assert read["favorites"] == {seed["resource"]["id"]}
    assert read["idempotency"]["response_payload"]["resource"]["rid"] == seed["stableRid"]
    assert read["otherTenantResources"] == []


def _assert_resource_catalog_round_trip(engine: Engine) -> None:
    repo = SqlAlchemyResourceCatalogRepository(engine)
    ctx = RequestContext(tenant_id=_TENANT, actor_user_id="owner-a", roles=("admin",))
    with _txn(engine) as transaction:
        project = _insert_project(repo, transaction, ctx)
        grant, updated_grant = _upsert_role_grants(repo, transaction, ctx, project["id"])
        folder, resource, updated_resource = _insert_folder_and_resource(repo, transaction, ctx, project["id"])
        _write_favorite_and_idempotency(repo, transaction, resource["id"], resource["rid"])
        read = _read_catalog(repo, transaction, project["id"])
    seed = {
        "project": project,
        "grant": grant,
        "updatedGrant": updated_grant,
        "folder": folder,
        "resource": resource,
        "updatedResource": updated_resource,
        "stableRid": resource["rid"],
    }
    _assert_catalog(seed, read)


def test_resource_catalog_repository_round_trip_sqlite() -> None:
    _assert_resource_catalog_round_trip(_sqlite_engine())


def test_resource_catalog_repository_defensive_write_helpers_raise_clear_errors() -> None:
    transition = catalog_repository_module._catalog_status_transition("archived")

    assert transition.from_statuses == ("active", "trashed")
    assert transition.to_status == "archived"
    with pytest.raises(RuntimeError, match="project write did not return a row"):
        catalog_repository_module._required(None)
    with pytest.raises(RuntimeError, match="project grant write did not return a row"):
        catalog_repository_module._required_grant(None)
    with pytest.raises(RuntimeError, match="project folder write did not return a row"):
        catalog_repository_module._required_folder(None)
    with pytest.raises(RuntimeError, match="resource write did not return a row"):
        catalog_repository_module._required_resource(None)


def test_resource_catalog_migration_applies_postgres_rls_to_tenant_tables() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2] / "migrations/versions/d8e0f2a4c6b9_projects_resources_catalog.py"
    )
    migration = migration_path.read_text(encoding="utf-8")

    assert 'context.get_context().dialect.name == "postgresql"' in migration
    assert "current_setting('foundry_lite.tenant_id', true)" in migration
    assert 'op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")' in migration
    assert 'op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")' in migration
    assert 'op.execute(f"CREATE POLICY {policy_name} ON {table_name}' in migration
    for table in _RESOURCE_TABLES:
        assert f'"{table}",' in migration


@skip_if_no_postgres
def test_resource_catalog_repository_round_trip_postgres(postgres_fixture: PostgresFixture) -> None:
    _assert_resource_catalog_round_trip(postgres_fixture.engine)
