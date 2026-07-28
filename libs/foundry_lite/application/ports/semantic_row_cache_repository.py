"""Durable rebuildable cache boundary for successful Pipeline semantic rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


@dataclass(frozen=True)
class SemanticRowCacheRecord:
    """One immutable successful semantic result under an exact execution fingerprint."""

    semantic_row_cache_id: str
    tenant_id: str
    cache_key: str
    request_fingerprint: str
    pipeline_id: str
    scope_kind: str
    scope_id: str
    node_id: str
    descriptor_id: str
    spec_version: str
    cache_generation: int
    resource_security_policy_fingerprint: str
    model_alias: str
    environment: str
    resolved_model_id: str
    resolved_model_revision: str
    provider: str
    prompt_version_id: str
    prompt_fingerprint: str
    input_fingerprint: str
    media_fingerprint: str
    output_schema_fingerprint: str
    config_fingerprint: str
    output_value: object
    model_evidence: Mapping[str, object]
    created_at: str


class SemanticRowCacheRepository(Protocol):
    """Tenant-scoped persistence for successful, rebuildable semantic row results."""

    def row_by_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        cache_key: str,
    ) -> SemanticRowCacheRecord | None:
        """Return the successful row stored under an exact tenant-scoped cache key."""
        ...

    def insert_success(
        self,
        *,
        transaction: TransactionContext,
        record: SemanticRowCacheRecord,
    ) -> SemanticRowCacheRecord:
        """Insert one successful result or return the immutable concurrent winner."""
        ...
