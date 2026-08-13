"""Concrete GitHub and Render readbacks for the hosted golden collector."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentServicePolicyObservation,
    InfrastructureDeploymentServicePolicyRequest,
)
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.ports.source_control_candidate import SourceRefSnapshot
from foundry_lite.application.ports.source_control_release import (
    SourceControlMergeReceipt,
    SourceControlMergeStatus,
    SourceControlReleasePort,
)
from foundry_lite.application.services.aip.external_release_delivery_payloads import (
    deployment_observation_ref,
    source_receipt_ref,
)
from foundry_lite.application.services.aip.external_release_infrastructure_evidence import (
    infrastructure_receipt_matches_delivery,
)
from foundry_lite.application.services.aip.external_release_source_publication_payloads import (
    source_publication_request_from_record,
    source_publication_target_from_record,
)
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
    ServerProviderReadback,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import canonical_digest
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

Provider = Literal["github", "render"]
DeliveryKey = tuple[ReleaseKind, DeliveryOperation]


@dataclass(frozen=True, slots=True)
class LiveProviderObservation:
    """One exact provider result plus its normalized public-safe evidence."""

    release_kind: ReleaseKind
    operation: DeliveryOperation
    delivery_id: str
    provider: Provider
    provider_resource_id: str
    ledger_result_fingerprint: str
    evidence_fingerprint: str
    provider_request_id: str
    is_exact_target: bool
    is_terminal_success: bool
    observed_at: datetime
    evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LiveTargetConfigurationObservation:
    """Fresh server-configured repository and Render policy readback."""

    fingerprint: str
    is_exact_target: bool
    provider_request_id: str
    observed_at: datetime
    evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LiveProviderSnapshot:
    """All six provider resources observed during one bounded pass."""

    observations: tuple[LiveProviderObservation, ...]
    target_configuration: LiveTargetConfigurationObservation
    completed_at: datetime


class GovernedReleaseLiveProviderCollector:
    """Read real provider state; injected adapter profiles are rejected."""

    def __init__(
        self,
        config: GovernedReleaseDeliveryConfig,
        source_control: SourceControlReleasePort,
        infrastructure: InfrastructureDeploymentAdapter,
    ) -> None:
        self._config = config
        self._source_control = source_control
        self._infrastructure = infrastructure

    def read_once(
        self,
        ctx: RequestContext,
        records: Sequence[ReleaseDeliveryRecord],
        collection_id: str,
    ) -> LiveProviderSnapshot:
        self._require_live_profiles()
        rows = _records_by_key(records)
        observations = tuple(self._read_row(ctx, row, rows, collection_id) for row in rows.values())
        target = self._target_configuration(ctx, collection_id)
        completed_at = max([target.observed_at, *(item.observed_at for item in observations)])
        return LiveProviderSnapshot(observations, target, completed_at)

    def _read_row(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        rows: Mapping[DeliveryKey, ReleaseDeliveryRecord],
        collection_id: str,
    ) -> LiveProviderObservation:
        if row.operation in {"source_publish", "source_merge"}:
            publication = row if row.operation == "source_publish" else _parent(rows, row)
            return self._source_observation(row, publication)
        return self._infrastructure_observation(ctx, row, collection_id)

    def _source_observation(
        self,
        row: ReleaseDeliveryRecord,
        publication: ReleaseDeliveryRecord,
    ) -> LiveProviderObservation:
        request = source_publication_request_from_record(publication, self._config)
        target = source_publication_target_from_record(publication, request)
        receipt = self._source_control.lookup_merge(target)
        evidence = _source_evidence(row, publication, receipt)
        provider_resource_id = _required_resource_id(row)
        is_exact = _source_receipt_matches(row, publication, receipt, provider_resource_id)
        return _observation(row, provider_resource_id, receipt.provider_request_id, is_exact, evidence)

    def _infrastructure_observation(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        collection_id: str,
    ) -> LiveProviderObservation:
        service_id = self._config.deployment_service_id
        if service_id is None:
            raise ValidationFailed("live collector requires a configured deployment service")
        provider_resource_id = _required_resource_id(row)
        observed = self._infrastructure.get(
            InfrastructureDeploymentGetRequest(
                ctx.tenant_id,
                service_id,
                provider_resource_id,
                ctx.request_id,
                collection_id,
            )
        )
        evidence = deployment_observation_ref(observed)
        is_exact = _infrastructure_observation_matches(row, observed, provider_resource_id)
        return _observation(row, provider_resource_id, observed.provider_request_id, is_exact, evidence)

    def _target_configuration(
        self,
        ctx: RequestContext,
        collection_id: str,
    ) -> LiveTargetConfigurationObservation:
        repository = self._config.source_repository
        service_id = self._config.deployment_service_id
        if repository is None or service_id is None:
            raise ValidationFailed("live collector requires server-owned GitHub and Render targets")
        source = self._source_control.inspect_source_ref(repository, self._config.source_base_ref)
        policy = self._infrastructure.get_service_policy(
            InfrastructureDeploymentServicePolicyRequest(ctx.tenant_id, service_id, ctx.request_id, collection_id)
        )
        evidence = _target_configuration_evidence(self._config, source, policy)
        is_exact = _target_configuration_matches(self._config, source, policy)
        return LiveTargetConfigurationObservation(
            _stable_evidence_fingerprint(evidence),
            is_exact,
            _required_text(policy.provider_request_id, "provider policy request id"),
            datetime.now(UTC),
            evidence,
        )

    def _require_live_profiles(self) -> None:
        profiles = (self._source_control.profile_name, self._infrastructure.profile_name)
        if profiles != ("github-release", "render-infrastructure-deployment"):
            raise ConflictDetected("live collector requires concrete GitHub and Render adapters")


def pair_provider_snapshots(
    initial: LiveProviderSnapshot,
    final: LiveProviderSnapshot,
) -> tuple[ServerProviderReadback, ...]:
    _require_exact_target_policy(initial.target_configuration, final.target_configuration)
    initial_by_key = {_observation_key(item): item for item in initial.observations}
    final_by_key = {_observation_key(item): item for item in final.observations}
    if set(initial_by_key) != set(final_by_key):
        raise ConflictDetected("provider readback resources changed during collection")
    return tuple(_paired_readback(initial_by_key[key], final_by_key[key]) for key in sorted(initial_by_key))


def _paired_readback(
    initial: LiveProviderObservation,
    final: LiveProviderObservation,
) -> ServerProviderReadback:
    _require_stable_observation_identity(initial, final)
    return ServerProviderReadback(
        release_kind=initial.release_kind,
        operation=initial.operation,
        delivery_id=initial.delivery_id,
        provider=initial.provider,
        provider_resource_id=initial.provider_resource_id,
        ledger_result_fingerprint=initial.ledger_result_fingerprint,
        initial_evidence_fingerprint=initial.evidence_fingerprint,
        final_evidence_fingerprint=final.evidence_fingerprint,
        provider_request_ids=(initial.provider_request_id, final.provider_request_id),
        is_exact_target=initial.is_exact_target and final.is_exact_target,
        is_terminal_success=initial.is_terminal_success and final.is_terminal_success,
        initial_observed_at=initial.observed_at,
        final_observed_at=final.observed_at,
    )


def _records_by_key(records: Sequence[ReleaseDeliveryRecord]) -> dict[DeliveryKey, ReleaseDeliveryRecord]:
    rows: dict[DeliveryKey, ReleaseDeliveryRecord] = {}
    for row in records:
        key = (row.release_kind, row.operation)
        if key in rows:
            raise ConflictDetected("live collection delivery set contains a duplicate operation")
        rows[key] = row
    expected: set[DeliveryKey] = {
        ("ontology", "source_publish"),
        ("ontology", "source_merge"),
        ("pipeline", "source_publish"),
        ("pipeline", "source_merge"),
        ("pipeline", "application_deploy"),
        ("pipeline", "application_rollback"),
    }
    if set(rows) != expected:
        raise ConflictDetected("live collection delivery set is incomplete")
    return dict(sorted(rows.items()))


def _parent(
    rows: Mapping[DeliveryKey, ReleaseDeliveryRecord],
    row: ReleaseDeliveryRecord,
) -> ReleaseDeliveryRecord:
    parent = rows.get((row.release_kind, "source_publish"))
    if parent is None or row.parent_delivery_id != parent.delivery_id:
        raise ConflictDetected("source merge is detached from its publication")
    return parent


def _source_evidence(
    row: ReleaseDeliveryRecord,
    publication: ReleaseDeliveryRecord,
    receipt: SourceControlMergeReceipt,
) -> dict[str, object]:
    evidence = source_receipt_ref(receipt)
    evidence.update(
        {
            "deliveryId": row.delivery_id,
            "publicationDeliveryId": publication.delivery_id,
            "manifestFingerprint": _candidate_text(publication, "manifestFingerprint"),
            "artifactPath": _candidate_text(publication, "artifactPath"),
        }
    )
    return evidence


def _source_receipt_matches(
    row: ReleaseDeliveryRecord,
    publication: ReleaseDeliveryRecord,
    receipt: SourceControlMergeReceipt,
    provider_resource_id: str,
) -> bool:
    published = publication.result_ref or {}
    expected_resource = f"pull:{receipt.pull_number}"
    actual = (
        receipt.status,
        row.status,
        publication.status,
        row.provider,
        publication.provider,
        receipt.repository_id,
        receipt.pull_number,
        receipt.head_sha,
        publication.provider_resource_id,
        provider_resource_id,
    )
    expected = (
        SourceControlMergeStatus.LANDED,
        "landed",
        "landed",
        "github",
        "github",
        publication.target_ref.get("repositoryId"),
        published.get("pullNumber"),
        published.get("headSha"),
        expected_resource,
        expected_resource,
    )
    if actual != expected or not receipt.provider_request_id:
        return False
    return _source_operation_receipt_matches(row, publication, receipt)


def _source_operation_receipt_matches(
    row: ReleaseDeliveryRecord,
    publication: ReleaseDeliveryRecord,
    receipt: SourceControlMergeReceipt,
) -> bool:
    if row.operation == "source_publish":
        return row.delivery_id == publication.delivery_id
    merge_sha = (row.result_ref or {}).get("mergeCommitSha")
    return isinstance(merge_sha, str) and receipt.merge_commit_sha == merge_sha


def _infrastructure_observation_matches(
    row: ReleaseDeliveryRecord,
    observed: InfrastructureDeploymentObservation,
    provider_resource_id: str,
) -> bool:
    is_exact_receipt = bool(
        row.status == "landed"
        and row.provider_resource_id == provider_resource_id == observed.deploy_id
        and infrastructure_receipt_matches_delivery(row, observed)
        and observed.provider_request_id
    )
    return is_exact_receipt and _deployment_lifecycle_matches(row, observed)


def _deployment_lifecycle_matches(
    row: ReleaseDeliveryRecord,
    observed: InfrastructureDeploymentObservation,
) -> bool:
    if row.operation == "application_deploy":
        return (
            observed.status == "deactivated"
            and observed.provider_status == "deactivated"
            and observed.is_terminal
            and not observed.is_successful
        )
    if row.operation == "application_rollback":
        return (
            observed.status == "live"
            and observed.provider_status == "live"
            and observed.is_terminal
            and observed.is_successful
        )
    return False


def _observation(
    row: ReleaseDeliveryRecord,
    provider_resource_id: str,
    provider_request_id: str | None,
    is_exact: bool,
    evidence: Mapping[str, object],
) -> LiveProviderObservation:
    request_id = _required_text(provider_request_id, "provider request id")
    return LiveProviderObservation(
        row.release_kind,
        row.operation,
        row.delivery_id,
        _provider(row.provider),
        provider_resource_id,
        canonical_digest(row.result_ref or {}),
        _stable_evidence_fingerprint(evidence),
        request_id,
        is_exact,
        is_exact,
        datetime.now(UTC),
        dict(evidence),
    )


def _target_configuration_evidence(
    config: GovernedReleaseDeliveryConfig,
    source: SourceRefSnapshot,
    policy: InfrastructureDeploymentServicePolicyObservation,
) -> dict[str, object]:
    repository = config.source_repository
    return {
        "sourceProvider": repository.provider if repository is not None else None,
        "repositoryId": repository.repository_id if repository is not None else None,
        "repositoryOwner": repository.owner if repository is not None else None,
        "repositoryName": repository.name if repository is not None else None,
        "baseRef": config.source_base_ref,
        "baseCommitSha": source.commit_sha,
        "baseTreeSha": source.tree_sha,
        "provider": policy.provider,
        "serviceId": policy.service_id,
        "isAutoDeployEnabled": policy.is_auto_deploy_enabled,
        "sourceRepositoryOwner": policy.source_repository_owner,
        "sourceRepositoryName": policy.source_repository_name,
        "sourceBranch": policy.source_branch,
        "serviceType": policy.service_type,
        "isSuspended": policy.is_suspended,
        "providerRequestId": policy.provider_request_id,
    }


def _target_configuration_matches(
    config: GovernedReleaseDeliveryConfig,
    source: SourceRefSnapshot,
    policy: InfrastructureDeploymentServicePolicyObservation,
) -> bool:
    repository = config.source_repository
    if repository is None:
        return False
    source_matches = (source.repository, source.ref) == (repository, config.source_base_ref)
    actual_policy = (
        policy.provider,
        policy.service_id,
        policy.is_auto_deploy_enabled,
        policy.source_repository_owner.casefold(),
        policy.source_repository_name.casefold(),
        policy.source_branch,
        policy.service_type,
        policy.is_suspended,
    )
    expected_policy = (
        "render",
        config.deployment_service_id,
        False,
        repository.owner.casefold(),
        repository.name.casefold(),
        config.source_base_ref,
        "web_service",
        False,
    )
    return source_matches and actual_policy == expected_policy


def _observation_key(item: LiveProviderObservation) -> tuple[ReleaseKind, DeliveryOperation, str]:
    return item.release_kind, item.operation, item.delivery_id


def _require_exact_target_policy(
    initial: LiveTargetConfigurationObservation,
    final: LiveTargetConfigurationObservation,
) -> None:
    if not initial.is_exact_target or not final.is_exact_target:
        raise ConflictDetected("provider target policy does not match the server-owned release target")
    _required_text(initial.provider_request_id, "initial provider policy request id")
    _required_text(final.provider_request_id, "final provider policy request id")


def _require_stable_observation_identity(
    initial: LiveProviderObservation,
    final: LiveProviderObservation,
) -> None:
    initial_identity = (initial.provider, initial.provider_resource_id, initial.ledger_result_fingerprint)
    final_identity = (final.provider, final.provider_resource_id, final.ledger_result_fingerprint)
    if initial_identity != final_identity:
        raise ConflictDetected("provider readback identity changed during collection")
    _required_text(initial.provider_request_id, "initial provider request id")
    _required_text(final.provider_request_id, "final provider request id")


def _candidate_text(row: ReleaseDeliveryRecord, key: str) -> str | None:
    value = row.candidate_ref.get(key) if row.candidate_ref is not None else None
    return value if isinstance(value, str) and value else None


def _stable_evidence_fingerprint(evidence: Mapping[str, object]) -> str:
    stable = dict(evidence)
    stable.pop("providerRequestId", None)
    return canonical_digest(stable)


def _required_resource_id(row: ReleaseDeliveryRecord) -> str:
    return _required_text(row.provider_resource_id, "provider resource id")


def _provider(value: str) -> Provider:
    if value == "github":
        return "github"
    if value == "render":
        return "render"
    raise ConflictDetected("live collector observed an unsupported provider")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConflictDetected(f"live collector requires {label}")
    return value.strip()


__all__ = [
    "GovernedReleaseLiveProviderCollector",
    "LiveProviderObservation",
    "LiveProviderSnapshot",
    "LiveTargetConfigurationObservation",
    "pair_provider_snapshots",
]
