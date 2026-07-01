"""Thin facade entrypoints for erasure gateway workflows."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports.erasure_repository import ErasureCertificateRecord
from foundry_lite.application.services.erasure_service import ErasureService
from foundry_lite.security.erasure import (
    ErasureCertificate,
    ErasureRequest,
    ErasureResolution,
    ErasureRetentionPolicy,
)


class ErasureGateway:
    """Privacy bounded context: run a durable subject erasure and read its certificate."""

    def __init__(self, service: ErasureService) -> None:
        self._service = service

    def run(
        self,
        request: ErasureRequest,
        resolutions: Sequence[ErasureResolution],
        *,
        retention_policy: ErasureRetentionPolicy,
    ) -> ErasureCertificate:
        return self._service.run_erasure(request, resolutions, retention_policy=retention_policy)

    def certificate_for(self, *, tenant_id: str, request_id: str) -> ErasureCertificateRecord | None:
        return self._service.certificate_for(tenant_id=tenant_id, request_id=request_id)
