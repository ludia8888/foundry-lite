"""Append-only trust boundary for hosted Governed Release verification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

from foundry_lite.application.ports.transaction_context import TransactionContext

LiveAttestationStatus = Literal["live_verified"]
LiveAttestationJson = Mapping[str, object]
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_APPLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RELEASE_SCOPE = "osdk:connector:governed_release:execute"
_CHATGPT_ORIGIN = "https://chatgpt.com"


class GovernedReleaseLiveAttestationConflict(RuntimeError):
    """One collector run was replayed with different immutable evidence."""


class GovernedReleaseLiveAttestationIntegrityError(RuntimeError):
    """A winning insert could not be read back from durable storage."""


@dataclass(frozen=True, slots=True)
class GovernedReleaseMcpAuthority:
    """Startup-owned public OAuth facts used by the hosted release MCP."""

    application_id: str = ""
    public_base_url: str = ""
    authorization_server_issuer: str = ""
    oauth_audience: str = ""
    allowed_client_ids: tuple[str, ...] = ()
    required_scope: str = _RELEASE_SCOPE
    trusted_origin: str = _CHATGPT_ORIGIN

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", self.application_id.strip())
        object.__setattr__(self, "public_base_url", self.public_base_url.strip().rstrip("/"))
        object.__setattr__(
            self,
            "authorization_server_issuer",
            self.authorization_server_issuer.strip().rstrip("/"),
        )
        object.__setattr__(self, "oauth_audience", self.oauth_audience.strip())
        clients = tuple(sorted({value.strip() for value in self.allowed_client_ids if value.strip()}))
        object.__setattr__(self, "allowed_client_ids", clients)

    @property
    def is_live_eligible(self) -> bool:
        return (
            _is_clean_https_url(self.public_base_url, is_origin=True)
            and _is_clean_https_url(self.authorization_server_issuer, is_origin=False)
            and _APPLICATION_ID.fullmatch(self.application_id) is not None
            and self.oauth_audience == self.release_resource(self.application_id)
            and bool(self.allowed_client_ids)
            and self.required_scope == _RELEASE_SCOPE
            and self.trusted_origin == _CHATGPT_ORIGIN
        )

    def release_resource(self, application_id: str) -> str:
        return f"{self.public_base_url}/mcp/release/{quote(application_id, safe='')}"


@dataclass(frozen=True, slots=True)
class GovernedReleaseLiveAuthority:
    """Infrastructure-owned facts that a request body cannot override."""

    runtime_profile: str
    database_backend: str
    source_provider_profile: str
    deployment_provider_profile: str
    source_provider_name: str
    deployment_provider_name: str
    is_source_provider_live: bool
    is_deployment_provider_live: bool
    source_revision: str
    collector_version: str = "governed-release-live-collector/v1"
    mcp_authority: GovernedReleaseMcpAuthority = field(default_factory=GovernedReleaseMcpAuthority)

    @property
    def is_live_eligible(self) -> bool:
        return (
            self.runtime_profile == "protected"
            and self.database_backend == "postgresql"
            and bool(self.source_provider_profile.strip())
            and bool(self.deployment_provider_profile.strip())
            and bool(self.source_provider_name.strip())
            and bool(self.deployment_provider_name.strip())
            and self.is_source_provider_live
            and self.is_deployment_provider_live
            and _GIT_SHA.fullmatch(self.source_revision) is not None
            and self.mcp_authority.is_live_eligible
        )

    def is_live_eligible_for(self, application_id: str) -> bool:
        return self.is_live_eligible and self.mcp_authority.application_id == application_id


def _is_clean_https_url(value: str, *, is_origin: bool) -> bool:
    parsed = urlsplit(value)
    clean = bool(
        parsed.scheme.lower() == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
    return clean and (not is_origin or parsed.path in {"", "/"})


@dataclass(frozen=True, slots=True)
class GovernedReleaseLiveAttestationRecord:
    """Server-collected proof for one exact hosted two-scenario run."""

    attestation_id: str
    tenant_id: str
    application_id: str
    collector_run_id: str
    schema_version: str
    status: LiveAttestationStatus
    attestation_fingerprint: str
    manifest_digest: str
    evidence_digest: str
    configuration_fingerprint: str
    collector_version: str
    source_revision: str
    runtime_profile: str
    database_backend: str
    source_provider_profile: str
    deployment_provider_profile: str
    ontology_workflow_run_id: str
    pipeline_workflow_run_id: str
    ontology_proposal_id: str
    pipeline_proposal_id: str
    evidence_json: LiveAttestationJson
    checks_json: LiveAttestationJson
    request_id: str
    created_by: str
    collected_at: str
    valid_until: str

    def __post_init__(self) -> None:
        """Reject records that cannot represent a real protected collector."""

        _require_record_identity(self)
        _require_record_digests(self)
        _require_record_authority(self)
        _require_record_scenarios(self)
        _require_record_times(self)


def _require_record_identity(record: GovernedReleaseLiveAttestationRecord) -> None:
    required = (
        record.attestation_id,
        record.tenant_id,
        record.application_id,
        record.collector_run_id,
        record.schema_version,
        record.collector_version,
        record.request_id,
        record.created_by,
    )
    if record.status != "live_verified" or not all(value.strip() for value in required):
        raise ValueError("live attestation identity and verified status are required")


def _require_record_digests(record: GovernedReleaseLiveAttestationRecord) -> None:
    digests = (
        record.attestation_fingerprint,
        record.manifest_digest,
        record.evidence_digest,
        record.configuration_fingerprint,
    )
    if not all(_SHA256.fullmatch(value) for value in digests):
        raise ValueError("live attestation digests must be full sha256 values")


def _require_record_authority(record: GovernedReleaseLiveAttestationRecord) -> None:
    if _GIT_SHA.fullmatch(record.source_revision) is None:
        raise ValueError("live attestation source revision must be a full Git SHA")
    if (record.runtime_profile, record.database_backend) != ("protected", "postgresql"):
        raise ValueError("live attestation requires the protected PostgreSQL runtime")
    profiles = (record.source_provider_profile, record.deployment_provider_profile)
    if not all(profile.strip() for profile in profiles):
        raise ValueError("live attestation requires concrete source and deployment adapter profiles")


def _require_record_scenarios(record: GovernedReleaseLiveAttestationRecord) -> None:
    required = (
        record.ontology_workflow_run_id,
        record.pipeline_workflow_run_id,
        record.ontology_proposal_id,
        record.pipeline_proposal_id,
    )
    if not all(value.strip() for value in required):
        raise ValueError("live attestation workflow and proposal identities are required")
    if record.ontology_workflow_run_id == record.pipeline_workflow_run_id:
        raise ValueError("live attestation scenarios require distinct workflow roots")
    if record.ontology_proposal_id == record.pipeline_proposal_id:
        raise ValueError("live attestation scenarios require distinct proposals")


def _require_record_times(record: GovernedReleaseLiveAttestationRecord) -> None:
    collected = _aware_timestamp(record.collected_at)
    if collected is None:
        raise ValueError("live attestation collection time must be timezone-aware")
    valid_until = _aware_timestamp(record.valid_until)
    if valid_until is None or valid_until <= collected:
        raise ValueError("live attestation validity must end after collection")


def _aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


GovernedReleaseLiveAttestationMutationResult = tuple[GovernedReleaseLiveAttestationRecord, bool]


class GovernedReleaseLiveAttestationRepository(Protocol):
    """Persist only completed live attestations; never accept a caller live flag."""

    def store_verified(
        self,
        *,
        transaction: TransactionContext,
        record: GovernedReleaseLiveAttestationRecord,
    ) -> GovernedReleaseLiveAttestationMutationResult:
        """Return ``(row, is_created)`` or fail on a conflicting run replay."""
        ...

    def get_by_collector_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        collector_run_id: str,
    ) -> GovernedReleaseLiveAttestationRecord | None: ...

    def latest_verified(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
    ) -> GovernedReleaseLiveAttestationRecord | None: ...


class UnavailableGovernedReleaseLiveAttestationRepository:
    """Read-safe default that can never manufacture a verified attestation."""

    def store_verified(
        self,
        *,
        transaction: TransactionContext,
        record: GovernedReleaseLiveAttestationRecord,
    ) -> GovernedReleaseLiveAttestationMutationResult:
        del transaction, record
        raise GovernedReleaseLiveAttestationIntegrityError("live attestation repository is unavailable")

    def get_by_collector_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        collector_run_id: str,
    ) -> GovernedReleaseLiveAttestationRecord | None:
        del transaction, tenant_id, application_id, collector_run_id
        return None

    def latest_verified(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
    ) -> GovernedReleaseLiveAttestationRecord | None:
        del transaction, tenant_id, application_id
        return None


__all__ = [
    "GovernedReleaseLiveAttestationConflict",
    "GovernedReleaseMcpAuthority",
    "GovernedReleaseLiveAuthority",
    "GovernedReleaseLiveAttestationIntegrityError",
    "GovernedReleaseLiveAttestationMutationResult",
    "GovernedReleaseLiveAttestationRecord",
    "GovernedReleaseLiveAttestationRepository",
    "LiveAttestationJson",
    "LiveAttestationStatus",
    "UnavailableGovernedReleaseLiveAttestationRepository",
]
