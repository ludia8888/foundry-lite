"""Fail-closed verification for the hosted Governed Release golden run.

The verifier deliberately separates structurally complete evidence from live
provider evidence.  Injected/local transports may exercise every parser branch,
but they can never set ``is_live_verified``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import quote, urlsplit

from foundry_lite.application.services.aip.governed_release_live_provider_receipts import (
    ProviderReceiptInvalid,
    ontology_receipt_contract,
    pipeline_receipt_contract,
    source_publication_manifest_contract,
    source_publication_receipt_contract,
)
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text

JsonObject = Mapping[str, object]
CheckStatus = Literal["passed", "blocked"]
ReadinessStatus = Literal["blocked", "ready_for_live_run", "live_verified"]

MANIFEST_SCHEMA = "governed-release-golden-manifest/v2"
EVIDENCE_SCHEMA = "governed-release-golden-evidence/v2"
VERIFICATION_SCHEMA = "governed-release-golden-verification/v2"
READINESS_SCHEMA = "governed-release-live-readiness/v2"
PREFLIGHT_SCHEMA = "governed-release-live-preflight/v1"
RELEASE_SCOPE = "osdk:connector:governed_release:execute"
LIVE_PREFLIGHT_ORIGIN = "live_provider_readback"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_DEPLOYMENT_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}$")


class LiveEvidenceInvalid(ValueError):
    """One normalized verification failure with a stable, secret-free code."""


@dataclass(frozen=True, slots=True)
class LiveEvidenceCheck:
    name: str
    status: CheckStatus
    code: str


@dataclass(frozen=True, slots=True)
class GoldenEvidenceVerification:
    schema_version: str
    run_id: str
    application_id: str
    status: Literal["blocked", "structurally_complete", "live_verified"]
    is_structurally_complete: bool
    is_live_verified: bool
    preflight_evidence_origin: str
    manifest_digest: str
    evidence_digest: str
    checks: tuple[LiveEvidenceCheck, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LiveReadinessReport:
    schema_version: str
    application_id: str
    status: ReadinessStatus
    is_ready_for_live_run: bool
    is_live_verified: bool
    checks: tuple[LiveEvidenceCheck, ...]
    blockers: tuple[str, ...]
    manifest_digest: str | None = None
    evidence_digest: str | None = None


def verify_golden_evidence(
    manifest: JsonObject,
    evidence: JsonObject,
    preflight: JsonObject,
) -> GoldenEvidenceVerification:
    """Verify exact bindings and keep injected/local proof non-live."""

    checks = (
        _capture("manifest", lambda: _manifest_contract(manifest)),
        _capture("preflight_bindings", lambda: _preflight_contract(manifest, preflight)),
        _capture("hosted_identity", lambda: _hosted_identity_contract(manifest, evidence)),
        _capture("ontology_scenario", lambda: _scenario_contract(manifest, evidence, "ontology")),
        _capture("pipeline_scenario", lambda: _scenario_contract(manifest, evidence, "pipeline")),
        _capture("live_provenance", lambda: _live_provenance_contract(evidence, preflight)),
        LiveEvidenceCheck("authentic_live_attestation", "blocked", "authentic_live_collector_required"),
    )
    structural = all(check.status == "passed" for check in checks[:-2])
    is_live = False
    blockers = tuple(check.code for check in checks if check.status == "blocked")
    return GoldenEvidenceVerification(
        schema_version=VERIFICATION_SCHEMA,
        run_id=_safe_text(manifest.get("runId")),
        application_id=_safe_text(manifest.get("applicationId")),
        status="structurally_complete" if structural else "blocked",
        is_structurally_complete=structural,
        is_live_verified=is_live,
        preflight_evidence_origin=_safe_text(preflight.get("evidence_origin")),
        manifest_digest=canonical_digest(manifest),
        evidence_digest=canonical_digest(evidence),
        checks=checks,
        blockers=blockers,
    )


def assess_live_readiness(
    application_id: str,
    manifest: JsonObject | None,
    preflight: JsonObject | None,
    verification: JsonObject | None,
) -> LiveReadinessReport:
    """Project operator artifacts into one safe authenticated endpoint result."""

    manifest_check = _capture("manifest", lambda: _readiness_manifest(application_id, manifest))
    preflight_check = _capture("live_preflight", lambda: _readiness_preflight(manifest, preflight))
    verification_check = _capture(
        "golden_write_evidence",
        lambda: _readiness_verification(application_id, manifest, verification),
    )
    checks = (manifest_check, preflight_check, verification_check)
    is_ready = manifest_check.status == "passed" and preflight_check.status == "passed"
    is_live = is_ready and verification_check.status == "passed"
    blockers = tuple(check.code for check in checks if check.status == "blocked")
    return LiveReadinessReport(
        schema_version=READINESS_SCHEMA,
        application_id=application_id,
        status="live_verified" if is_live else "ready_for_live_run" if is_ready else "blocked",
        is_ready_for_live_run=is_ready,
        is_live_verified=is_live,
        checks=checks,
        blockers=blockers,
        manifest_digest=canonical_digest(manifest) if manifest is not None else None,
        evidence_digest=_verification_evidence_digest(verification),
    )


def serialize_verification(result: GoldenEvidenceVerification | LiveReadinessReport) -> str:
    return json.dumps(asdict(result), indent=2, sort_keys=True) + "\n"


def canonical_digest(payload: JsonObject) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _manifest_contract(manifest: JsonObject) -> None:
    _expect(manifest, "schemaVersion", MANIFEST_SCHEMA, "manifest_schema_mismatch")
    _text(manifest, "runId")
    application_id = _text(manifest, "applicationId")
    public_base = _https_origin(_text(manifest, "publicBaseUrl"))
    _https_url(_text(manifest, "authorizationServer"))
    _text(manifest, "hostedClientId")
    _expect(manifest, "requiredScope", RELEASE_SCOPE, "manifest_scope_mismatch")
    resource = f"{public_base}/mcp/release/{quote(application_id, safe='')}"
    _expect(manifest, "resource", resource, "manifest_resource_mismatch")
    source = _mapping(manifest, "sourceControl")
    _source_target_contract(source)
    deployment = _mapping(manifest, "deployment")
    _deployment_target_contract(deployment)
    required = _text_sequence(manifest, "requiredScenarios")
    if set(required) != {"ontology", "pipeline"} or len(required) != 2:
        raise LiveEvidenceInvalid("manifest_required_scenarios_mismatch")


def _preflight_contract(manifest: JsonObject, preflight: JsonObject) -> None:
    _expect(preflight, "schema_version", PREFLIGHT_SCHEMA, "preflight_schema_mismatch")
    if preflight.get("is_ready") is not True or preflight.get("status") != "ready":
        raise LiveEvidenceInvalid("preflight_not_ready")
    configuration = _preflight_check_evidence(preflight, "configuration")
    source = _mapping(manifest, "sourceControl")
    deployment = _mapping(manifest, "deployment")
    expected = {
        "publicBaseUrl": _text(manifest, "publicBaseUrl"),
        "authorizationServer": _text(manifest, "authorizationServer"),
        "resource": _text(manifest, "resource"),
        "repository": f"{_text(source, 'owner')}/{_text(source, 'repository')}",
        "repositoryId": _integer(source, "repositoryId"),
        "baseBranch": _text(source, "baseRef"),
        "serviceId": _text(deployment, "serviceId"),
    }
    if any(configuration.get(key) != value for key, value in expected.items()):
        raise LiveEvidenceInvalid("preflight_target_binding_mismatch")


def _hosted_identity_contract(manifest: JsonObject, evidence: JsonObject) -> None:
    _expect(evidence, "schemaVersion", EVIDENCE_SCHEMA, "evidence_schema_mismatch")
    _same_text(manifest, evidence, "runId", "evidence_run_mismatch")
    _same_text(manifest, evidence, "applicationId", "evidence_application_mismatch")
    capture = _mapping(evidence, "capture")
    _expect(capture, "host", "chatgpt.com", "hosted_chatgpt_host_mismatch")
    _expect(capture, "publicEndpoint", _text(manifest, "resource"), "capture_endpoint_mismatch")
    principals = _mapping(evidence, "principals")
    submitter = _mapping(principals, "submitter")
    reviewer = _mapping(principals, "reviewer")
    _principal_contract(manifest, submitter)
    _principal_contract(manifest, reviewer)


def _principal_contract(manifest: JsonObject, principal: JsonObject) -> None:
    _pattern(principal, "subjectHash", _SHA256, "principal_subject_hash_invalid")
    _pattern(principal, "oauthSessionHash", _SHA256, "principal_session_hash_invalid")
    _expect(principal, "applicationId", _text(manifest, "applicationId"), "principal_application_mismatch")
    _expect(principal, "clientId", _text(manifest, "hostedClientId"), "principal_client_mismatch")
    _expect(principal, "issuer", _text(manifest, "authorizationServer"), "principal_issuer_mismatch")
    _expect(principal, "audience", _text(manifest, "resource"), "principal_audience_mismatch")
    _expect(principal, "grantType", "authorization_code", "principal_grant_mismatch")
    _expect(principal, "oauthSessionAuthority", "issuer", "principal_session_authority_mismatch")
    if principal.get("isHuman") is not True:
        raise LiveEvidenceInvalid("principal_human_grant_missing")


def _scenario_contract(manifest: JsonObject, evidence: JsonObject, kind: str) -> None:
    scenario = _scenario(evidence, kind)
    governance = _mapping(scenario, "governance")
    principals = _mapping(evidence, "principals")
    submitter = _mapping(principals, "submitter")
    reviewer = _mapping(principals, "reviewer")
    _governance_contract(governance, submitter, reviewer)
    source = _mapping(scenario, "sourceControl")
    source_target = _mapping(manifest, "sourceControl")
    source_publication_receipt_contract(
        source_target,
        kind,
        _text(governance, "proposalId"),
        _mapping(scenario, "sourcePublication"),
        source,
    )
    _source_receipt_contract(source_target, source)
    if kind == "ontology":
        ontology_receipt_contract(scenario)
        return
    pipeline_receipt_contract(_mapping(manifest, "deployment"), scenario, source)


def _governance_contract(governance: JsonObject, submitter: JsonObject, reviewer: JsonObject) -> None:
    _text(governance, "proposalId")
    _pattern(governance, "proposalFingerprint", _SHA256, "proposal_fingerprint_invalid")
    if governance.get("validationPassed") is not True:
        raise LiveEvidenceInvalid("proposal_validation_not_passed")
    _expect(governance, "decision", "approve", "proposal_not_approved")
    _expect(
        governance,
        "submitterSubjectHash",
        _text(submitter, "subjectHash"),
        "proposal_submitter_mismatch",
    )
    _expect(governance, "reviewerSubjectHash", _text(reviewer, "subjectHash"), "proposal_reviewer_mismatch")
    audit_ids = _text_sequence(governance, "auditEventIds")
    if len(set(audit_ids)) < 3:
        raise LiveEvidenceInvalid("governance_audit_evidence_incomplete")


def _source_target_contract(source: JsonObject) -> None:
    _text(source, "provider")
    _integer(source, "repositoryId")
    _text(source, "owner")
    _text(source, "repository")
    _text(source, "baseRef")
    source_publication_manifest_contract(source)


def _source_receipt_contract(target: JsonObject, receipt: JsonObject) -> None:
    for key in ("provider", "repositoryId", "owner", "repository", "baseRef"):
        _expect(receipt, key, target.get(key), f"source_{key}_mismatch")
    _integer(receipt, "pullNumber")
    _pattern(receipt, "headSha", _GIT_SHA, "source_head_sha_invalid")
    _pattern(receipt, "mergeCommitSha", _GIT_SHA, "source_merge_sha_invalid")
    if receipt.get("isMerged") is not True or receipt.get("requiredChecksPassed") is not True:
        raise LiveEvidenceInvalid("source_merge_or_checks_unverified")
    _text(receipt, "providerRequestId")
    _timestamp(receipt, "mergedAt")


def _deployment_target_contract(deployment: JsonObject) -> None:
    _text(deployment, "provider")
    _pattern(deployment, "serviceId", _DEPLOYMENT_SERVICE, "deployment_service_id_invalid")
    if deployment.get("environment") not in {"staging", "production"}:
        raise LiveEvidenceInvalid("deployment_environment_mismatch")


def _live_provenance_contract(evidence: JsonObject, preflight: JsonObject) -> None:
    if preflight.get("evidence_origin") != LIVE_PREFLIGHT_ORIGIN:
        raise LiveEvidenceInvalid("live_provider_preflight_required")
    capture = _mapping(evidence, "capture")
    if capture.get("isSimulated") is not False:
        raise LiveEvidenceInvalid("simulated_evidence_cannot_be_live")
    _expect(capture, "transportProfile", "hosted_chatgpt_public_https", "live_transport_profile_required")
    _expect(capture, "verificationProfile", "provider_live_readback", "live_readback_profile_required")
    _timestamp(capture, "completedAt")


def _readiness_manifest(application_id: str, manifest: JsonObject | None) -> None:
    if manifest is None:
        raise LiveEvidenceInvalid("golden_manifest_missing")
    _manifest_contract(manifest)
    _expect(manifest, "applicationId", application_id, "readiness_application_mismatch")


def _readiness_preflight(manifest: JsonObject | None, preflight: JsonObject | None) -> None:
    if manifest is None or preflight is None:
        raise LiveEvidenceInvalid("live_preflight_missing")
    _preflight_contract(manifest, preflight)
    if preflight.get("evidence_origin") != LIVE_PREFLIGHT_ORIGIN:
        raise LiveEvidenceInvalid("live_provider_preflight_required")


def _readiness_verification(
    application_id: str,
    manifest: JsonObject | None,
    verification: JsonObject | None,
) -> None:
    if manifest is None or verification is None:
        raise LiveEvidenceInvalid("golden_write_evidence_missing")
    _expect(verification, "schema_version", VERIFICATION_SCHEMA, "verification_schema_mismatch")
    _expect(verification, "application_id", application_id, "verification_application_mismatch")
    _expect(verification, "manifest_digest", canonical_digest(manifest), "verification_manifest_digest_mismatch")
    raise LiveEvidenceInvalid("authentic_live_collector_required")


def _scenario(evidence: JsonObject, kind: str) -> JsonObject:
    scenarios = _sequence(evidence, "scenarios")
    matching = [item for item in scenarios if isinstance(item, Mapping) and item.get("kind") == kind]
    if len(matching) != 1 or len(scenarios) != 2:
        raise LiveEvidenceInvalid(f"{kind}_scenario_missing_or_duplicated")
    return matching[0]


def _preflight_check_evidence(preflight: JsonObject, name: str) -> JsonObject:
    checks = _sequence(preflight, "checks")
    matching = [item for item in checks if isinstance(item, Mapping) and item.get("name") == name]
    if len(matching) != 1 or matching[0].get("status") != "ready":
        raise LiveEvidenceInvalid(f"preflight_{name}_not_ready")
    evidence = matching[0].get("evidence")
    if not isinstance(evidence, Mapping):
        raise LiveEvidenceInvalid(f"preflight_{name}_evidence_missing")
    return evidence


def _capture(name: str, operation: Callable[[], None]) -> LiveEvidenceCheck:
    try:
        operation()
    except LiveEvidenceInvalid as exc:
        return LiveEvidenceCheck(name, "blocked", scrub_error_text(str(exc)))
    except ProviderReceiptInvalid as exc:
        return LiveEvidenceCheck(name, "blocked", scrub_error_text(str(exc)))
    except Exception:
        return LiveEvidenceCheck(name, "blocked", f"{name}_invalid")
    return LiveEvidenceCheck(name, "passed", "verified")


def _mapping(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise LiveEvidenceInvalid(f"{key}_object_required")
    return value


def _sequence(payload: JsonObject, key: str) -> Sequence[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise LiveEvidenceInvalid(f"{key}_list_required")
    return value


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LiveEvidenceInvalid(f"{key}_text_required")
    return value.strip()


def _safe_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _integer(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LiveEvidenceInvalid(f"{key}_positive_integer_required")
    return value


def _text_sequence(payload: JsonObject, key: str) -> tuple[str, ...]:
    values = _sequence(payload, key)
    if not values or not all(isinstance(value, str) and value.strip() for value in values):
        raise LiveEvidenceInvalid(f"{key}_text_list_required")
    return tuple(str(value).strip() for value in values)


def _expect(payload: JsonObject, key: str, expected: object, code: str) -> None:
    if payload.get(key) != expected:
        raise LiveEvidenceInvalid(code)


def _same_text(left: JsonObject, right: JsonObject, key: str, code: str) -> None:
    if _text(left, key) != _text(right, key):
        raise LiveEvidenceInvalid(code)


def _pattern(payload: JsonObject, key: str, pattern: re.Pattern[str], code: str) -> str:
    value = _text(payload, key)
    if pattern.fullmatch(value) is None:
        raise LiveEvidenceInvalid(code)
    return value


def _timestamp(payload: JsonObject, key: str) -> str:
    value = _text(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LiveEvidenceInvalid(f"{key}_timestamp_invalid") from None
    if parsed.tzinfo is None:
        raise LiveEvidenceInvalid(f"{key}_timezone_required")
    return value


def _https_origin(value: str) -> str:
    parsed = urlsplit(value)
    is_invalid = (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
    )
    if is_invalid:
        raise LiveEvidenceInvalid("public_https_origin_required")
    return value.rstrip("/")


def _https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise LiveEvidenceInvalid("authorization_server_https_required")
    return value.rstrip("/")


def _verification_evidence_digest(verification: JsonObject | None) -> str | None:
    if verification is None:
        return None
    value = verification.get("evidence_digest")
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


__all__ = [
    "EVIDENCE_SCHEMA",
    "GoldenEvidenceVerification",
    "LIVE_PREFLIGHT_ORIGIN",
    "LiveEvidenceCheck",
    "LiveReadinessReport",
    "MANIFEST_SCHEMA",
    "PREFLIGHT_SCHEMA",
    "READINESS_SCHEMA",
    "VERIFICATION_SCHEMA",
    "assess_live_readiness",
    "canonical_digest",
    "serialize_verification",
    "verify_golden_evidence",
]
