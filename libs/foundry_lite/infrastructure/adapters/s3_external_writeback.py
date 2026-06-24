"""Real S3/MinIO external-writeback adapter (L8: action external side effect against a vendor).

This is the first **real** ``ExternalWritebackAdapter``. An ontology action's before-commit
external write becomes a real ``PUT`` to ``s3://<bucket>/<key>`` on an external system (the key is
derived from the action idempotency key, so a replay PUTs the same object — idempotent, not a blind
new write). The three reach-out shapes map exactly to the port contract:

- a successful ``PUT`` → ``LANDED`` (carrying the object key as the remote_resource_id);
- a timeout / connection error while writing → ``AMBIGUOUS`` (the write may have landed remotely,
  so the service maps it to ``outcome_unknown``, NOT failure);
- ``remote_lookup`` HEADs the same key — a present object resolves to ``LANDED``, a genuine
  missing-object to ``ABSENT``, and an unreachable source raises (transient, retry bounded).

Mirrors ``S3ExternalMediaReader``: same ``build_s3_client`` connection (reusing ``FOUNDRY_LITE_S3_*``
env, no new env var) and the same ``is_not_found_error`` classifier that separates a real 404 from a
transient connection error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackPayload,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
    WriteReceipt,
)
from foundry_lite.infrastructure.adapters.s3_client import build_s3_client, is_not_found_error


@dataclass(frozen=True)
class S3ExternalWritebackConfig:
    bucket: str
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = field(default=None, repr=False)
    region_name: str = "us-east-1"
    key_prefix: str = "external-writebacks"


def _is_timeout_or_connection_error(exc: Exception) -> bool:
    """Classify a write error as ambiguous (timeout/connection) rather than a clean rejection."""
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "connect", "endpoint")) or isinstance(
        exc, (TimeoutError, OSError)
    )


class S3ExternalWritebackAdapter:
    """``ExternalWritebackAdapter`` that PUTs/HEADs a real S3 object (profile ``s3-external-writeback``)."""

    profile_name = "s3-external-writeback"

    def __init__(self, config: S3ExternalWritebackConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client or build_s3_client(
            endpoint_url=config.endpoint_url,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            region_name=config.region_name,
        )

    def _object_key(self, target: ExternalWriteTarget) -> str:
        return f"{self.config.key_prefix.strip('/')}/{target.idempotency_key}"

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        key = self._object_key(target)
        body = json.dumps({"uri": target.uri, "payload": dict(payload)}, sort_keys=True).encode("utf-8")
        try:
            self._client.put_object(Bucket=self.config.bucket, Key=key, Body=body)
        except Exception as exc:
            if _is_timeout_or_connection_error(exc):
                return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS, detail={"reason": "external_write_ambiguous"})
            raise
        return WriteReceipt(status=RemoteOutcomeStatus.LANDED, remote_resource_id=key)

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        key = self._object_key(target)
        try:
            self._client.head_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            if is_not_found_error(exc):
                return RemoteOutcome(status=RemoteOutcomeStatus.ABSENT)
            raise
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id=key)
