"""Exact internal and Render receipt checks for a hosted golden run."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime

JsonObject = Mapping[str, object]
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_RENDER_DEPLOY = re.compile(r"^dep-[A-Za-z0-9_-]{3,128}$")
_SOURCE_MANIFEST_PATH = re.compile(
    r"^\.foundry-lite/releases/(?P<kind>ontology|pipeline)/"
    r"(?P<proposal>[A-Za-z0-9][A-Za-z0-9_-]{0,127})\.json$"
)
_SOURCE_BINDING_KEYS = (
    "provider",
    "repositoryId",
    "owner",
    "repository",
    "baseRef",
    "baseSha",
    "headRef",
    "headSha",
    "treeSha",
    "pullNumber",
    "manifestPath",
    "manifestFingerprint",
)


class ProviderReceiptInvalid(ValueError):
    """Stable, secret-free receipt failure code."""


def source_publication_manifest_contract(target: JsonObject) -> None:
    """Require one exact source-publication expectation per golden scenario."""

    publications = _sequence(target, "publications")
    if len(publications) != 2:
        raise ProviderReceiptInvalid("source_publication_manifest_count_mismatch")
    for kind in ("ontology", "pipeline"):
        _publication_expectation(target, publications, kind, proposal_id=None)


def source_publication_receipt_contract(
    target: JsonObject,
    kind: str,
    proposal_id: str,
    publication: JsonObject,
    merge: JsonObject,
) -> None:
    """Bind durable publication, exact Git artifact, and later merge receipt."""

    expected = _publication_expectation(target, _sequence(target, "publications"), kind, proposal_id)
    _publication_ledger_contract(publication)
    _publication_binding_contract(expected, publication)
    _publication_merge_contract(publication, merge)


def _publication_expectation(
    target: JsonObject,
    publications: Sequence[object],
    kind: str,
    proposal_id: str | None,
) -> JsonObject:
    matching = [item for item in publications if isinstance(item, Mapping) and item.get("kind") == kind]
    if len(matching) != 1:
        raise ProviderReceiptInvalid(f"{kind}_source_publication_manifest_missing_or_duplicated")
    expected = matching[0]
    _publication_expectation_contract(target, expected, kind)
    if proposal_id is not None:
        _expect(expected, "proposalId", proposal_id, "source_publication_proposal_mismatch")
    return expected


def _publication_expectation_contract(target: JsonObject, expected: JsonObject, kind: str) -> None:
    _expect(expected, "kind", kind, "source_publication_kind_mismatch")
    _expect(expected, "operation", "source_publish", "source_publication_operation_mismatch")
    proposal_id = _text(expected, "proposalId")
    for key in ("provider", "repositoryId", "owner", "repository", "baseRef"):
        _expect(expected, key, target.get(key), f"source_publication_{key}_mismatch")
    for key in ("baseSha", "headSha", "treeSha"):
        _pattern(expected, key, _GIT_SHA, f"source_publication_{key}_invalid")
    _text(expected, "headRef")
    _integer(expected, "pullNumber")
    _publication_manifest_contract(expected, kind, proposal_id)


def _publication_manifest_contract(expected: JsonObject, kind: str, proposal_id: str) -> None:
    path = _pattern(expected, "manifestPath", _SOURCE_MANIFEST_PATH, "source_manifest_path_invalid")
    match = _SOURCE_MANIFEST_PATH.fullmatch(path)
    if match is None or match.group("kind") != kind or match.group("proposal") != proposal_id:
        raise ProviderReceiptInvalid("source_manifest_path_identity_mismatch")
    _pattern(expected, "manifestFingerprint", _SHA256, "source_manifest_fingerprint_invalid")


def _publication_ledger_contract(publication: JsonObject) -> None:
    _text(publication, "deliveryId")
    _expect(publication, "operation", "source_publish", "source_publication_operation_mismatch")
    _expect(publication, "status", "landed", "source_publication_not_landed")
    _expect(publication, "receiptStatus", "published", "source_publication_receipt_not_published")
    pull_number = _integer(publication, "pullNumber")
    _expect(
        publication,
        "providerResourceId",
        f"pull:{pull_number}",
        "source_publication_resource_identity_mismatch",
    )
    _text(publication, "providerRequestId")
    _timestamp(publication, "completedAt")


def _publication_binding_contract(expected: JsonObject, publication: JsonObject) -> None:
    _expect(publication, "proposalId", expected.get("proposalId"), "source_publication_proposal_mismatch")
    for key in _SOURCE_BINDING_KEYS:
        _expect(publication, key, expected.get(key), f"source_publication_{key}_mismatch")


def _publication_merge_contract(publication: JsonObject, merge: JsonObject) -> None:
    _expect(
        merge,
        "publicationDeliveryId",
        publication.get("deliveryId"),
        "source_merge_publication_receipt_mismatch",
    )
    for key in _SOURCE_BINDING_KEYS:
        _expect(merge, key, publication.get(key), f"source_merge_publication_{key}_mismatch")


def ontology_receipt_contract(scenario: JsonObject) -> None:
    internal = _mapping(scenario, "internal")
    rollback = _mapping(scenario, "rollback")
    _expect(internal, "status", "active", "ontology_activation_unverified")
    active_version = _integer(internal, "activeVersionNumber")
    _text(internal, "auditEventId")
    _expect(rollback, "status", "rolled_back", "ontology_rollback_unverified")
    target_version = _integer(rollback, "targetVersionNumber")
    if target_version == active_version:
        raise ProviderReceiptInvalid("ontology_rollback_target_not_prior")
    _text(rollback, "auditEventId")


def pipeline_receipt_contract(target: JsonObject, scenario: JsonObject, source: JsonObject) -> None:
    internal = _mapping(scenario, "internal")
    _expect(internal, "status", "PROMOTED", "pipeline_promotion_unverified")
    _text(internal, "deploymentId")
    _text(internal, "auditEventId")
    deployment = _mapping(scenario, "deployment")
    observation = _mapping(scenario, "statusObservation")
    rollback = _mapping(scenario, "rollback")
    _render_live_contract(target, deployment, _text(source, "mergeCommitSha"))
    _render_observation_contract(deployment, observation)
    _render_rollback_contract(target, deployment, rollback)


def _render_live_contract(target: JsonObject, receipt: JsonObject, commit_id: str) -> None:
    for key in ("provider", "serviceId", "environment"):
        _expect(receipt, key, target.get(key), f"render_{key}_mismatch")
    _pattern(receipt, "deployId", _RENDER_DEPLOY, "render_deploy_id_invalid")
    _expect(receipt, "commitId", commit_id, "render_commit_mismatch")
    _expect(receipt, "status", "live", "render_deploy_not_live")
    if receipt.get("isTerminal") is not True or receipt.get("isSuccessful") is not True:
        raise ProviderReceiptInvalid("render_deploy_not_terminal_success")
    _text(receipt, "providerRequestId")
    _timestamp(receipt, "finishedAt")


def _render_observation_contract(deployment: JsonObject, observation: JsonObject) -> None:
    for key in ("deployId", "commitId"):
        _expect(observation, key, deployment.get(key), f"render_status_{key}_mismatch")
    _expect(observation, "status", "live", "render_status_not_live")
    _text(observation, "providerRequestId")
    _timestamp(observation, "observedAt")


def _render_rollback_contract(target: JsonObject, deployment: JsonObject, rollback: JsonObject) -> None:
    for key in ("provider", "serviceId", "environment"):
        _expect(rollback, key, target.get(key), f"rollback_{key}_mismatch")
    current_deploy = _text(deployment, "deployId")
    _expect(rollback, "rolledBackFromDeployId", current_deploy, "rollback_current_deploy_mismatch")
    target_deploy = _pattern(rollback, "targetDeployId", _RENDER_DEPLOY, "rollback_target_deploy_invalid")
    rollback_deploy = _pattern(rollback, "rollbackDeployId", _RENDER_DEPLOY, "rollback_deploy_id_invalid")
    if len({current_deploy, target_deploy, rollback_deploy}) != 3:
        raise ProviderReceiptInvalid("rollback_deploy_identity_reused")
    _pattern(rollback, "targetCommitId", _GIT_SHA, "rollback_target_commit_invalid")
    _expect(rollback, "commitId", rollback.get("targetCommitId"), "rollback_commit_mismatch")
    _expect(rollback, "trigger", "rollback", "rollback_trigger_mismatch")
    _expect(rollback, "status", "live", "rollback_not_live")
    if rollback.get("isTerminal") is not True or rollback.get("isSuccessful") is not True:
        raise ProviderReceiptInvalid("rollback_not_terminal_success")
    _text(rollback, "providerRequestId")
    _timestamp(rollback, "finishedAt")


def _mapping(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ProviderReceiptInvalid(f"{key}_object_required")
    return value


def _sequence(payload: JsonObject, key: str) -> Sequence[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ProviderReceiptInvalid(f"{key}_list_required")
    return value


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderReceiptInvalid(f"{key}_text_required")
    return value.strip()


def _integer(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProviderReceiptInvalid(f"{key}_positive_integer_required")
    return value


def _expect(payload: JsonObject, key: str, expected: object, code: str) -> None:
    if payload.get(key) != expected:
        raise ProviderReceiptInvalid(code)


def _pattern(payload: JsonObject, key: str, pattern: re.Pattern[str], code: str) -> str:
    value = _text(payload, key)
    if pattern.fullmatch(value) is None:
        raise ProviderReceiptInvalid(code)
    return value


def _timestamp(payload: JsonObject, key: str) -> None:
    value = _text(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderReceiptInvalid(f"{key}_timestamp_invalid") from None
    if parsed.tzinfo is None:
        raise ProviderReceiptInvalid(f"{key}_timezone_required")


__all__ = [
    "ProviderReceiptInvalid",
    "ontology_receipt_contract",
    "pipeline_receipt_contract",
    "source_publication_manifest_contract",
    "source_publication_receipt_contract",
]
