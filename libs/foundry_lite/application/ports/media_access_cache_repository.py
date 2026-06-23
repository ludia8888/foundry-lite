"""DB boundary for on-demand access-pattern caches (Media/Content Plane M9).

A cache row points at preview bytes for one (source version + access pattern + spec). It is
TTL/evictable/rebuildable and is NEVER serving truth: it pins ``source_content_hash`` so a
stale cache (its source changed) is detectable and re-rendered, and only this access-pattern
service reads it — references and content retrieval read the committed source, not the cache
(ADR-0001 invariant: ``access_pattern_cache_is_not_truth``). CAS / uniqueness / durable-row
operations only; the service owns the transaction lifecycle and the domain decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


@dataclass(frozen=True)
class MediaAccessCacheRecord:
    """Durable row for one cached access-pattern preview (a rebuildable view, never truth)."""

    access_cache_id: str
    tenant_id: str
    source_media_item_version_id: str
    access_pattern: str
    access_pattern_spec_hash: str
    persistence_policy: str
    source_content_hash: str
    blob_key: str
    content_hash: str
    byte_size: int
    mime_type: str
    created_at: str
    expires_at: str | None = None


class MediaAccessCacheRepository(Protocol):
    """DB boundary for access-pattern cache rows."""

    def access_cache_by_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        source_media_item_version_id: str,
        access_pattern: str,
        access_pattern_spec_hash: str,
    ) -> MediaAccessCacheRecord | None:
        """Return one cache row by its (tenant, source version, pattern, spec) key."""
        ...

    def upsert_access_cache(
        self, *, transaction: TransactionContext, record: MediaAccessCacheRecord
    ) -> MediaAccessCacheRecord:
        """Insert or replace the cache row for its unique key, returning the stored row."""
        ...

    def delete_access_caches_for_version(
        self, *, transaction: TransactionContext, tenant_id: str, source_media_item_version_id: str
    ) -> list[str]:
        """Evict all cache rows for one source version, returning the deleted ids."""
        ...
