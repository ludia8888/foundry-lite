"""Contract for ``MediaAccessCacheRepository`` (Media/Content Plane M9).

Durable-row operations for the on-demand access-pattern cache (sqlite + PostgreSQL): a row is
fetched by its (tenant, source version, pattern, spec) key, upsert replaces the row for that
key (a recompute always reflects the latest source), and eviction by source version returns the
deleted ids. The cache is never serving truth — the service owns the recompute decision.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRecord
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaAccessCacheRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture(params=["sqlalchemy", "postgres"])
def cache_repo(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[SqlAlchemyMediaAccessCacheRepository, Engine]:
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'cache.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyMediaAccessCacheRepository(engine), engine
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyMediaAccessCacheRepository(postgres_fixture.engine), postgres_fixture.engine


def _record(access_cache_id: str = "mac-1", *, content_hash: str = "h1") -> MediaAccessCacheRecord:
    return MediaAccessCacheRecord(
        access_cache_id=access_cache_id,
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-1",
        access_pattern="thumbnail",
        access_pattern_spec_hash="spec-1",
        persistence_policy="persist",
        source_content_hash="src-hash",
        blob_key="cache/miv-1/thumbnail/spec-1",
        content_hash=content_hash,
        byte_size=128,
        mime_type="image/png",
        created_at="2026-06-24T00:00:00Z",
    )


def test_upsert_replaces_row_for_the_same_key(
    cache_repo: tuple[SqlAlchemyMediaAccessCacheRepository, Engine],
) -> None:
    repo, engine = cache_repo
    with engine.begin() as conn:
        repo.upsert_access_cache(transaction=conn, record=_record())
        repo.upsert_access_cache(transaction=conn, record=_record(access_cache_id="mac-2", content_hash="h2"))
    with engine.begin() as conn:
        found = repo.access_cache_by_key(
            transaction=conn,
            tenant_id="tenant-demo",
            source_media_item_version_id="miv-1",
            access_pattern="thumbnail",
            access_pattern_spec_hash="spec-1",
        )
    assert found is not None
    assert found.access_cache_id == "mac-2"  # the latest upsert replaced the prior row
    assert found.content_hash == "h2"


def test_delete_evicts_caches_for_a_version(
    cache_repo: tuple[SqlAlchemyMediaAccessCacheRepository, Engine],
) -> None:
    repo, engine = cache_repo
    with engine.begin() as conn:
        repo.upsert_access_cache(transaction=conn, record=_record())
        deleted = repo.delete_access_caches_for_version(
            transaction=conn, tenant_id="tenant-demo", source_media_item_version_id="miv-1"
        )
        remaining = repo.access_cache_by_key(
            transaction=conn,
            tenant_id="tenant-demo",
            source_media_item_version_id="miv-1",
            access_pattern="thumbnail",
            access_pattern_spec_hash="spec-1",
        )
    assert deleted == ["mac-1"]
    assert remaining is None
