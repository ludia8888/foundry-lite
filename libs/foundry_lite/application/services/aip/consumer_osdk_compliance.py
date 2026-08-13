"""Fail-closed validation for source-bound consumer OSDK build receipts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]
_SCHEMA = "foundry-lite-consumer-osdk-compliance/v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def consumer_osdk_compliance_binding(
    receipt: JsonObject,
    application_id: str,
    expected_source_commit: str,
) -> dict[str, object]:
    """Return a public-safe binding after verifying one strict app receipt."""

    _require_receipt_header(receipt, expected_source_commit)
    application = _application(receipt, application_id)
    _require_application(application, application_id)
    return {
        "schemaVersion": _SCHEMA,
        "applicationId": application_id,
        "sourceCommit": expected_source_commit,
        "inventoryHash": _sha(receipt, "inventoryHash"),
        "ontologyFingerprint": _sha(application, "ontologyFingerprint"),
        "sourceTreeHash": _sha(application, "sourceTreeHash"),
        "sdkPackageTreeHash": _sha(application, "sdkPackageTreeHash"),
        "sdkArtifactHash": _sha(application, "sdkArtifactHash"),
        "receiptFingerprint": _fingerprint(receipt),
    }


def _require_receipt_header(receipt: JsonObject, expected_source_commit: str) -> None:
    if receipt.get("schemaVersion") != _SCHEMA or receipt.get("isCompliant") is not True:
        raise ValidationFailed("consumer OSDK compliance receipt is not a passing v1 receipt")
    source_commit = receipt.get("sourceCommit")
    if not isinstance(source_commit, str) or _GIT_SHA.fullmatch(source_commit) is None:
        raise ValidationFailed("consumer OSDK compliance receipt requires a full source commit")
    if source_commit != expected_source_commit:
        raise ConflictDetected(
            "consumer OSDK compliance receipt is stale for the release source commit",
            details={"expectedSourceCommit": expected_source_commit, "receiptSourceCommit": source_commit},
        )
    _sha(receipt, "inventoryHash")


def _application(receipt: JsonObject, application_id: str) -> JsonObject:
    values = receipt.get("applications")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValidationFailed("consumer OSDK compliance receipt applications are required")
    matches = [item for item in values if isinstance(item, Mapping) and item.get("applicationId") == application_id]
    if len(matches) != 1:
        raise ValidationFailed("consumer OSDK compliance receipt must contain the exact application once")
    return matches[0]


def _require_application(application: JsonObject, application_id: str) -> None:
    is_clean = (
        application.get("profile") == "consumer_osdk_strict"
        and application.get("isCompliant") is True
        and application.get("violationCount") == 0
        and application.get("exceptionCount") == 0
    )
    if not is_clean:
        raise ConflictDetected(
            "consumer OSDK strict application has bypasses or unresolved violations",
            details={"applicationId": application_id},
        )
    for key in ("ontologyFingerprint", "sourceTreeHash", "sdkPackageTreeHash", "sdkArtifactHash"):
        _sha(application, key)


def _sha(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationFailed(f"consumer OSDK compliance {key} must be a sha256 fingerprint")
    return value


def _fingerprint(payload: JsonObject) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


__all__ = ["consumer_osdk_compliance_binding"]
