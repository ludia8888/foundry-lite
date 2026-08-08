"""Typed OSDK MCP persistence records shared by application boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from foundry_lite.application.ports.runtime_repository import RuntimeJsonObject


class OsdkMcpServerRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    status: str
    description_markdown: str
    allowed_origins: list[str]
    last_activity_at: str | None
    updated_by_user_id: str
    created_at: str
    updated_at: str


class OsdkMcpSessionRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    client_id: str
    actor_user_id: str
    status: str
    last_sequence: int
    created_at: str
    last_seen_at: str
    terminated_at: str | None
    stream_lease_id: str | None
    stream_lease_expires_at: str | None


class OsdkMcpSessionEventRow(TypedDict):
    id: str
    tenant_id: str
    session_id: str
    sequence: int
    event_type: str
    payload_json: RuntimeJsonObject
    created_at: str


@dataclass(frozen=True)
class OsdkMcpServerRecord:
    server_id: str
    tenant_id: str
    app_id: str
    status: str
    description_markdown: str
    allowed_origins: tuple[str, ...]
    last_activity_at: str | None
    updated_by_user_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OsdkMcpSessionRecord:
    session_id: str
    tenant_id: str
    app_id: str
    client_id: str
    actor_user_id: str
    status: str
    created_at: str
    last_seen_at: str


@dataclass(frozen=True)
class OsdkMcpStreamLease:
    lease_id: str
    expires_at: str
