"""External media reader for virtual media sets (Media/Content Plane M9 + L6).

A virtual media set is a *pointer* to an object in an external source system — Foundry never
copies the bytes. v1 (M9) shipped the contract + the version/marker invariant with the connector
deferred, so the default reader reports ``external_reader_unavailable`` (mirroring live-OCR /
live-ASR) and tests inject a fake. L6 ships the first **real** connector, ``S3ExternalMediaReader``
(profile ``s3-external``): it HEADs a real ``s3://<bucket>/<key>`` object and reports its current
ETag + size, so the virtual set stays a pointer (no byte copy). Because a virtual set "is not aware
of source updates/deletions", the caller pins that ETag and re-validates it on every resolve (drift
fails closed) or marks the reference mutable (freshness surfaced).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from foundry_lite.application.ports.adapter_failure import (
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.external_media_reader import (
    ExternalObjectStat,
    ExternalReadError,
    ExternalReadRequest,
)
from foundry_lite.infrastructure.adapters.s3_client import build_s3_client, is_not_found_error

# uri -> current external stat (raises ExternalReadError when no connector is bundled).
ExternalStatEngine = Callable[[str], ExternalObjectStat]


def _default_external_stat(uri: str) -> ExternalObjectStat:
    # No external connector is bundled; a real profile injects one. Shipping the contract +
    # marker invariant now and deferring the live connector mirrors the live-OCR deferral.
    del uri
    raise ExternalReadError("external_reader_unavailable")


class LocalExternalMediaReader:
    """``ExternalMediaReader`` whose connector is injectable (profile ``local-external``)."""

    profile_name = "local-external"

    def __init__(self, *, stat_engine: ExternalStatEngine | None = None) -> None:
        self._stat_engine = stat_engine or _default_external_stat

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "stat",
                    "unavailable",
                    True,
                    "No external connector is bundled, or the external source is unreachable; retry bounded.",
                ),
            ),
        )

    def stat(self, request: ExternalReadRequest) -> ExternalObjectStat:
        return self._stat_engine(request.uri)


@dataclass(frozen=True)
class S3ExternalMediaReaderConfig:
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = field(default=None, repr=False)
    region_name: str = "us-east-1"


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ExternalReadError("external_uri_not_s3")
    return parsed.netloc, parsed.path.lstrip("/")


def _normalize_etag(raw: object) -> str:
    return str(raw or "").strip('"')


class S3ExternalMediaReader:
    """``ExternalMediaReader`` that HEADs a real S3 object (profile ``s3-external``)."""

    profile_name = "s3-external"

    def __init__(self, config: S3ExternalMediaReaderConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client or build_s3_client(
            endpoint_url=config.endpoint_url,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            region_name=config.region_name,
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "stat",
                    "unavailable",
                    True,
                    "The external S3 source could not be reached while HEADing the object; retry bounded.",
                ),
            ),
        )

    def stat(self, request: ExternalReadRequest) -> ExternalObjectStat:
        bucket, key = _parse_s3_uri(request.uri)
        try:
            head = self._client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if is_not_found_error(exc):
                return ExternalObjectStat(uri=request.uri, etag="", byte_size=0, is_present=False)
            raise ExternalReadError("external_source_unreachable") from exc
        return ExternalObjectStat(
            uri=request.uri,
            etag=_normalize_etag(head.get("ETag")),
            byte_size=int(head.get("ContentLength", 0)),
            is_present=True,
        )
