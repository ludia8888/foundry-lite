from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class WebhookEventKeyRecord:
    webhook_event_key_id: str
    tenant_id: str
    dataset_id: str
    connector_name: str
    resource_name: str
    event_id: str
    transaction_id: str
    committed_version_id: str
    payload_hash: str
    created_at: str


class WebhookEventKeyRow(TypedDict):
    id: str
    tenant_id: str
    dataset_id: str
    connector_name: str
    resource_name: str
    event_id: str
    transaction_id: str
    committed_version_id: str
    payload_hash: str
    created_at: str
