"""Application service helpers for osdk application types workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import OsdkReleaseStatus, RuntimeJsonObject, TransactionContext
from foundry_lite.domain.context import RequestContext

_LANGUAGES = frozenset({"typescript", "python"})
_BUMP_TYPES = frozenset({"patch", "minor", "major"})
_RESOURCE_TYPES = frozenset({"object", "action", "link", "function"})
_OPERATIONS = frozenset({"read", "subscribe", "validate", "execute"})
_CHANNELS = frozenset({"stable", "beta", "latest"})
_DEFAULT_DOWNLOAD_TOKEN_TTL_SECONDS = 900
_DEFAULT_COMPATIBILITY_DAYS = 30


class OsdkRuntimeAuditBoundary(Protocol):
    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class _ReleaseDecision:
    version: str
    compatibility_status: str
    release_status: OsdkReleaseStatus
    required_bump: str


@dataclass(frozen=True)
class _ArtifactDraft:
    kind: str
    path: Path
    content_hash: str
    metadata_json: RuntimeJsonObject
