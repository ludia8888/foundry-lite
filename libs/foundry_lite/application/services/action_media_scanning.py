"""Security preparation for untrusted Action media and attachment uploads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from foundry_lite.application.ports.action_file_scanner import (
    ActionFileScanner,
    ActionFileScanRequest,
    ActionFileScanResult,
    ActionFileScanUnavailable,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.upload_limits import max_media_upload_bytes
from foundry_lite.domain.action_runtime.action_contract import ActionParameterV3
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ExternalSystemError, ValidationFailed


@dataclass(frozen=True, slots=True)
class ActionParameterUpload:
    action_api_name: str
    parameter_name: str
    object_type: str
    object_id: str
    file_name: str
    source: BinaryIO
    supplied_mime_type: str
    idempotency_key: str
    format: str | None
    ctx: RequestContext


@dataclass(frozen=True, slots=True)
class PreparedActionUpload:
    scan: ActionFileScanResult
    request_fingerprint: str


def prepare_action_upload(
    upload: ActionParameterUpload,
    parameter: ActionParameterV3,
    scanner: ActionFileScanner,
    engine: TransactionManager,
    runtime: ActionRuntimeBoundary,
) -> PreparedActionUpload:
    content_hash, size_bytes = source_fingerprint(upload.source)
    _require_upload_size(parameter, size_bytes)
    scan = _scan_upload(upload, scanner, engine, runtime, content_hash, size_bytes)
    fingerprint = _upload_fingerprint(upload, content_hash)
    return PreparedActionUpload(scan, fingerprint)


def _scan_upload(
    upload: ActionParameterUpload,
    scanner: ActionFileScanner,
    engine: TransactionManager,
    runtime: ActionRuntimeBoundary,
    content_hash: str,
    size_bytes: int,
) -> ActionFileScanResult:
    request = ActionFileScanRequest(
        upload.source, upload.file_name, upload.supplied_mime_type, content_hash, size_bytes
    )
    try:
        result = scanner.scan(request)
    except ActionFileScanUnavailable as exc:
        _record_scan_rejection(upload, scanner, engine, runtime, content_hash, "unavailable", None)
        raise ExternalSystemError("Action file upload could not be malware-scanned safely") from exc
    if result.verdict == "infected":
        _record_scan_rejection(upload, scanner, engine, runtime, content_hash, result.verdict, result.threat_name)
        raise ValidationFailed(
            "Action file upload was rejected by malware scanning",
            details={
                "parameter": upload.parameter_name,
                "verdict": result.verdict,
                "threatName": result.threat_name,
            },
        )
    return result


def _record_scan_rejection(
    upload: ActionParameterUpload,
    scanner: ActionFileScanner,
    engine: TransactionManager,
    runtime: ActionRuntimeBoundary,
    content_hash: str,
    verdict: str,
    threat_name: str | None,
) -> None:
    with engine.begin() as transaction:
        runtime._audit(
            transaction,
            upload.ctx,
            event_type="action.media.scan.rejected",
            resource_type="action_type",
            resource_id=upload.action_api_name,
            action="upload",
            decision="deny",
            after_ref={
                "parameter": upload.parameter_name,
                "contentSha256": content_hash,
                "scanner": scanner.profile_name,
                "verdict": verdict,
                "threatName": threat_name,
            },
        )


def source_fingerprint(source: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    source.seek(0)
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
        size_bytes += len(chunk)
    source.seek(0)
    return digest.hexdigest(), size_bytes


def scan_evidence(scan: ActionFileScanResult) -> dict[str, object]:
    return {
        "verdict": scan.verdict,
        "scanner": scan.scanner,
        "engineVersion": scan.engine_version,
        "signatureDatabaseVersion": scan.signature_database_version,
        "threatName": scan.threat_name,
    }


def _require_upload_size(parameter: ActionParameterV3, size_bytes: int) -> None:
    configured = parameter.metadata.get("maxBytes")
    maximum = (
        configured if isinstance(configured, int) and not isinstance(configured, bool) else max_media_upload_bytes()
    )
    maximum = min(maximum, max_media_upload_bytes())
    if size_bytes > maximum:
        raise ValidationFailed(
            "Action media upload exceeds the parameter size limit",
            details={"parameter": parameter.api_name, "sizeBytes": size_bytes, "maxBytes": maximum},
        )


def _upload_fingerprint(upload: ActionParameterUpload, content_hash: str) -> str:
    return _json_hash(
        {
            "actionApiName": upload.action_api_name,
            "parameter": upload.parameter_name,
            "objectType": upload.object_type,
            "objectId": upload.object_id,
            "fileName": upload.file_name,
            "mimeType": upload.supplied_mime_type,
            "contentHash": content_hash,
        }
    )


__all__ = [
    "ActionParameterUpload",
    "PreparedActionUpload",
    "prepare_action_upload",
    "scan_evidence",
    "source_fingerprint",
]
