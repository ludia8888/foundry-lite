"""Fail-closed contract for typed server ledger and provider readbacks."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ReleaseKind = Literal["ontology", "pipeline"]
ReleaseTool = str
DeliveryOperation = Literal["source_publish", "source_merge", "application_deploy", "application_rollback"]
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ServerActionClaim:
    release_kind: ReleaseKind
    proposal_id: str
    tool_name: ReleaseTool
    ai_run_id: str
    binding_hash: str
    authorization_fingerprint: str
    actor_subject_hash: str
    oauth_session_hash: str
    mcp_session_id: str
    idempotency_key: str
    status: str
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class ServerDeliveryClaim:
    release_kind: ReleaseKind
    proposal_id: str
    operation: DeliveryOperation
    delivery_id: str
    workflow_run_id: str
    parent_delivery_id: str | None
    provider: str
    provider_resource_id: str
    ai_run_id: str
    binding_hash: str
    result_fingerprint: str
    status: str
    dispatch_started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class ServerProviderReadback:
    release_kind: ReleaseKind
    operation: DeliveryOperation
    delivery_id: str
    provider: str
    provider_resource_id: str
    ledger_result_fingerprint: str
    initial_evidence_fingerprint: str
    final_evidence_fingerprint: str
    provider_request_ids: tuple[str, str]
    is_exact_target: bool
    is_terminal_success: bool
    initial_observed_at: datetime
    final_observed_at: datetime


@dataclass(frozen=True, slots=True)
class ServerCollectionSeal:
    """First and final fingerprints over the selected server evidence rows."""

    collection_id: str
    tenant_id: str
    application_id: str
    golden_run_id: str
    initial_database_fingerprint: str
    final_database_fingerprint: str
    initial_target_configuration_fingerprint: str
    final_target_configuration_fingerprint: str
    initial_read_at: datetime
    final_read_at: datetime


@dataclass(frozen=True, slots=True)
class ServerLoadedCollectionClaim:
    """Typed boundary that caller JSON cannot satisfy directly."""

    authority: str
    collection_id: str
    tenant_id: str
    golden_run_id: str
    application_id: str
    database_system: str
    provider_readback_mode: str
    source_provider_name: str
    deployment_provider_name: str
    authorization_policy_fingerprint: str
    submitter_subject_hash: str
    submitter_oauth_session_hash: str
    reviewer_subject_hash: str
    reviewer_oauth_session_hash: str
    is_authorization_code_human_grant: bool
    actions: tuple[ServerActionClaim, ...]
    deliveries: tuple[ServerDeliveryClaim, ...]
    provider_readbacks: tuple[ServerProviderReadback, ...]
    seal: ServerCollectionSeal
    collection_started_at: datetime
    collection_completed_at: datetime


@dataclass(frozen=True, slots=True)
class LiveCollectionContractResult:
    """Eligibility result for a later durable attestation write."""

    collection_id: str
    is_authoritative: bool
    is_chronologically_valid: bool
    is_toctou_stable: bool
    is_attestation_eligible: bool
    blockers: tuple[str, ...]


ActionIndex = dict[tuple[ReleaseKind, ReleaseTool], ServerActionClaim]
DeliveryIndex = dict[tuple[ReleaseKind, DeliveryOperation], ServerDeliveryClaim]
ReadbackIndex = dict[tuple[ReleaseKind, DeliveryOperation], ServerProviderReadback]
_ACTION_SEQUENCE: dict[ReleaseKind, tuple[ReleaseTool, ...]] = {
    "ontology": tuple(
        "publish_release_candidate assign_release_reviewer submit_release_decision "
        "execute_approved_release rollback_release".split()
    ),
    "pipeline": tuple(
        "publish_release_candidate assign_release_reviewer submit_release_decision "
        "execute_approved_release deploy_release rollback_release".split()
    ),
}
_ACTION_KEYS = frozenset((kind, tool) for kind, tools in _ACTION_SEQUENCE.items() for tool in tools)
_DELIVERY_TOOL: dict[DeliveryOperation, ReleaseTool] = {
    "source_publish": "publish_release_candidate",
    "source_merge": "execute_approved_release",
    "application_deploy": "deploy_release",
    "application_rollback": "rollback_release",
}
_DELIVERY_KEYS = frozenset(
    (kind, operation)
    for kind in _ACTION_SEQUENCE
    for operation in _DELIVERY_TOOL
    if kind == "pipeline" or operation.startswith("source_")
)


def assess_server_loaded_collection(claim: ServerLoadedCollectionClaim) -> LiveCollectionContractResult:
    """Assess whether trusted evidence may be persisted as a live attestation."""
    if type(claim) is not ServerLoadedCollectionClaim:
        raise TypeError("server_loaded_collection_claim_required")
    shape = _shape_blockers(claim)
    if shape:
        return _result(claim.collection_id, shape, ("chronology_not_evaluable",), ("toctou_not_evaluable",))
    return _result(
        claim.collection_id, _authority_blockers(claim), _chronology_blockers(claim), _toctou_blockers(claim)
    )


def _result(
    collection_id: str, authority: tuple[str, ...], chronology: tuple[str, ...], toctou: tuple[str, ...]
) -> LiveCollectionContractResult:
    blockers = _unique([*authority, *chronology, *toctou])
    flags = (not authority, not chronology, not toctou, not blockers)
    return LiveCollectionContractResult(collection_id, *flags, blockers)


def _shape_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    checks = (
        (claim.actions, ServerActionClaim, "server_action_claims_required"),
        (claim.deliveries, ServerDeliveryClaim, "server_delivery_claims_required"),
        (claim.provider_readbacks, ServerProviderReadback, "server_provider_readbacks_required"),
    )
    blockers = [
        code
        for values, item_type, code in checks
        if type(values) is not tuple or not all(type(value) is item_type for value in values)
    ]
    if type(claim.seal) is not ServerCollectionSeal:
        blockers.append("server_collection_seal_required")
    return tuple(blockers)


def _authority_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    blockers = [
        *_context_blockers(claim),
        *_action_blockers(claim),
        *_delivery_blockers(claim),
        *_readback_blockers(claim),
    ]
    scope = (claim.collection_id, claim.tenant_id, claim.application_id, claim.golden_run_id)
    seal_scope = (claim.seal.collection_id, claim.seal.tenant_id, claim.seal.application_id, claim.seal.golden_run_id)
    if scope != seal_scope:
        blockers.append("collection_seal_scope_mismatch")
    return _unique(blockers)


def _context_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    blockers: list[str] = []
    if claim.authority != "server_postgresql_provider_readback":
        blockers.append("server_collection_authority_required")
    if claim.database_system != "postgresql":
        blockers.append("postgresql_authoritative_ledger_required")
    if claim.provider_readback_mode != "concrete_network":
        blockers.append("concrete_provider_readback_required")
    texts: tuple[str, ...] = (claim.collection_id, claim.tenant_id, claim.golden_run_id, claim.application_id)
    texts = (*texts, claim.source_provider_name, claim.deployment_provider_name)
    if not all(_text(value) for value in texts):
        blockers.append("server_collection_scope_invalid")
    identity_hashes = (
        claim.authorization_policy_fingerprint,
        claim.submitter_subject_hash,
        claim.submitter_oauth_session_hash,
        claim.reviewer_subject_hash,
        claim.reviewer_oauth_session_hash,
    )
    if not _hashes(*identity_hashes) or not claim.is_authorization_code_human_grant:
        blockers.append("issuer_authoritative_human_grants_required")
    return tuple(blockers)


def _action_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    blockers: list[str] = []
    actions = _actions(claim.actions)
    if len(claim.actions) != len(_ACTION_KEYS) or set(actions) != _ACTION_KEYS:
        blockers.append("golden_action_set_mismatch")
    for action in claim.actions:
        blockers.extend(_action_claim_blockers(action, claim))
    if not _valid_proposals(claim.actions):
        blockers.append("scenario_proposal_binding_mismatch")
    return _unique(blockers)


def _action_claim_blockers(action: ServerActionClaim, claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    texts = (action.proposal_id, action.ai_run_id, action.mcp_session_id, action.idempotency_key)
    hashes = (action.binding_hash, action.authorization_fingerprint)
    blockers: list[str] = []
    if not all(_text(value) for value in texts) or not _hashes(*hashes) or action.status != "succeeded":
        blockers.append("action_ledger_binding_invalid")
    if action.authorization_fingerprint != claim.authorization_policy_fingerprint:
        blockers.append("action_authorization_binding_mismatch")
    if not _action_actor_matches(action, claim):
        blockers.append("action_principal_binding_mismatch")
    return tuple(blockers)


def _action_actor_matches(action: ServerActionClaim, claim: ServerLoadedCollectionClaim) -> bool:
    if action.tool_name == "publish_release_candidate":
        expected = (claim.submitter_subject_hash, claim.submitter_oauth_session_hash)
    else:
        expected = (claim.reviewer_subject_hash, claim.reviewer_oauth_session_hash)
    return (action.actor_subject_hash, action.oauth_session_hash) == expected


def _valid_proposals(actions: tuple[ServerActionClaim, ...]) -> bool:
    proposals = [{item.proposal_id for item in actions if item.release_kind == kind} for kind in _ACTION_SEQUENCE]
    return (
        all(len(values) == 1 and all(_text(value) for value in values) for values in proposals)
        and len({value for values in proposals for value in values}) == 2
    )


def _delivery_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    blockers: list[str] = []
    deliveries = _deliveries(claim.deliveries)
    actions = _actions(claim.actions)
    if len(claim.deliveries) != len(_DELIVERY_KEYS) or set(deliveries) != _DELIVERY_KEYS:
        blockers.append("golden_delivery_set_mismatch")
    for delivery in deliveries.values():
        tool = _DELIVERY_TOOL.get(delivery.operation)
        action = actions.get((delivery.release_kind, tool)) if tool is not None else None
        if action is None or not _valid_delivery_binding(delivery, claim):
            blockers.append("delivery_ledger_binding_invalid")
        elif not _delivery_matches_action(delivery, action):
            blockers.append("delivery_action_binding_mismatch")
    blockers.extend(_lineage_blockers(deliveries))
    return _unique(blockers)


def _valid_delivery_binding(
    delivery: ServerDeliveryClaim,
    claim: ServerLoadedCollectionClaim,
) -> bool:
    texts = (delivery.proposal_id, delivery.delivery_id, delivery.workflow_run_id, delivery.provider_resource_id)
    expected_provider = (
        claim.source_provider_name if delivery.operation.startswith("source_") else claim.deployment_provider_name
    )
    return all(
        (
            all(_text(value) for value in texts),
            delivery.provider == expected_provider,
            delivery.status == "landed",
            _hashes(delivery.binding_hash, delivery.result_fingerprint),
        )
    )


def _delivery_matches_action(delivery: ServerDeliveryClaim, action: ServerActionClaim) -> bool:
    identity = (delivery.proposal_id, delivery.ai_run_id, delivery.binding_hash)
    return identity == (action.proposal_id, action.ai_run_id, action.binding_hash)


def _lineage_blockers(deliveries: DeliveryIndex) -> tuple[str, ...]:
    checks = [check for kind in _ACTION_SEQUENCE for check in _source_lineage_checks(deliveries, kind)]
    checks.extend(_application_lineage_checks(deliveries))
    return tuple(code for is_valid, code in checks if not is_valid)


def _source_lineage_checks(deliveries: DeliveryIndex, kind: ReleaseKind) -> tuple[tuple[bool, str], ...]:
    publish = deliveries.get((kind, "source_publish"))
    merge = deliveries.get((kind, "source_merge"))
    is_root = (
        publish is not None and publish.parent_delivery_id is None and publish.workflow_run_id == publish.ai_run_id
    )
    is_merge_child = not is_root or (publish is not None and merge is not None and _child_of(merge, publish))
    return ((is_root, "source_publication_root_invalid"), (is_merge_child, "source_merge_parent_invalid"))


def _application_lineage_checks(deliveries: DeliveryIndex) -> tuple[tuple[bool, str], ...]:
    merge = deliveries.get(("pipeline", "source_merge"))
    deploy = deliveries.get(("pipeline", "application_deploy"))
    rollback = deliveries.get(("pipeline", "application_rollback"))
    deploy_ok = merge is not None and deploy is not None and _child_of(deploy, merge)
    rollback_ok = deploy is not None and rollback is not None and _child_of(rollback, deploy)
    return ((deploy_ok, "application_deploy_parent_invalid"), (rollback_ok, "application_rollback_parent_invalid"))


def _child_of(child: ServerDeliveryClaim, parent: ServerDeliveryClaim) -> bool:
    return child.parent_delivery_id == parent.delivery_id and child.workflow_run_id == parent.workflow_run_id


def _readback_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    """Bind each concrete provider read to one exact delivery row."""
    blockers: list[str] = []
    readbacks = _readbacks(claim.provider_readbacks)
    deliveries = _deliveries(claim.deliveries)
    if len(claim.provider_readbacks) != len(_DELIVERY_KEYS) or set(readbacks) != _DELIVERY_KEYS:
        blockers.append("golden_provider_readback_set_mismatch")
    for key, readback in readbacks.items():
        delivery = deliveries.get(key)
        if delivery is None or not _readback_matches(readback, delivery):
            blockers.append("provider_readback_delivery_binding_mismatch")
        if not readback.is_exact_target or not readback.is_terminal_success:
            blockers.append("provider_readback_outcome_unverified")
    return _unique(blockers)


def _readback_matches(read: ServerProviderReadback, delivery: ServerDeliveryClaim) -> bool:
    identity = (read.delivery_id, read.provider, read.provider_resource_id, read.ledger_result_fingerprint)
    expected = (delivery.delivery_id, delivery.provider, delivery.provider_resource_id, delivery.result_fingerprint)
    return (
        identity == expected
        and len(read.provider_request_ids) == 2
        and all(_text(value) for value in read.provider_request_ids)
    )


def _chronology_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    """Validate mutation, delivery, and sealed read ordering."""
    if not all(_aware(value) for value in _timestamps(claim)):
        return ("timezone_aware_collection_timestamps_required",)
    blockers: list[str] = []
    if claim.collection_started_at > claim.collection_completed_at:
        blockers.append("collection_window_invalid")
    blockers.extend(_action_time_blockers(claim))
    blockers.extend(_delivery_time_blockers(claim))
    blockers.extend(_readback_time_blockers(claim))
    return _unique(blockers)


def _action_time_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    blockers: list[str] = []
    actions = _actions(claim.actions)
    if any(not (item.started_at <= item.completed_at <= claim.collection_started_at) for item in claim.actions):
        blockers.append("action_outside_pre_collection_window")
    for kind, sequence in _ACTION_SEQUENCE.items():
        if _is_action_sequence_reversed(actions, kind, sequence):
            blockers.append(f"{kind}_action_order_invalid")
    return tuple(blockers)


def _is_action_sequence_reversed(actions: ActionIndex, kind: ReleaseKind, sequence: tuple[ReleaseTool, ...]) -> bool:
    ordered = [actions.get((kind, tool)) for tool in sequence]
    if any(item is None for item in ordered):
        return False
    pairs = zip(ordered, ordered[1:], strict=False)
    return any(left is not None and right is not None and left.completed_at > right.started_at for left, right in pairs)


def _delivery_time_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    actions = _actions(claim.actions)
    for delivery in claim.deliveries:
        tool = _DELIVERY_TOOL.get(delivery.operation)
        action = actions.get((delivery.release_kind, tool)) if tool is not None else None
        if action is None:
            continue
        is_ordered = (
            action.started_at
            <= delivery.dispatch_started_at
            <= delivery.completed_at
            <= action.completed_at
            <= claim.collection_started_at
        )
        if not is_ordered:
            return ("delivery_outside_action_window",)
    return ()


def _readback_time_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    seal = claim.seal
    if not (claim.collection_started_at <= seal.initial_read_at <= seal.final_read_at <= claim.collection_completed_at):
        return ("collection_seal_window_invalid",)
    for readback in claim.provider_readbacks:
        is_ordered = (
            seal.initial_read_at <= readback.initial_observed_at <= readback.final_observed_at <= seal.final_read_at
        )
        if not is_ordered:
            return ("provider_readback_outside_sealed_window",)
    return ()


def _toctou_blockers(claim: ServerLoadedCollectionClaim) -> tuple[str, ...]:
    """Reject selected DB, target, or provider evidence drift."""
    seal = claim.seal
    hashes = (
        seal.initial_database_fingerprint,
        seal.final_database_fingerprint,
        seal.initial_target_configuration_fingerprint,
        seal.final_target_configuration_fingerprint,
    )
    blockers: list[str] = []
    if not _hashes(*hashes):
        blockers.append("collection_seal_fingerprint_invalid")
    if hashes[0] != hashes[1]:
        blockers.append("database_snapshot_changed_during_collection")
    if hashes[2] != hashes[3]:
        blockers.append("target_configuration_changed_during_collection")
    for readback in claim.provider_readbacks:
        values = (
            readback.ledger_result_fingerprint,
            readback.initial_evidence_fingerprint,
            readback.final_evidence_fingerprint,
        )
        if not _hashes(*values):
            blockers.append("provider_readback_fingerprint_invalid")
        if values[1] != values[2]:
            blockers.append("provider_evidence_changed_during_collection")
    return _unique(blockers)


def _timestamps(claim: ServerLoadedCollectionClaim) -> tuple[datetime, ...]:
    actions = tuple(value for item in claim.actions for value in (item.started_at, item.completed_at))
    deliveries = tuple(value for item in claim.deliveries for value in (item.dispatch_started_at, item.completed_at))
    readbacks = tuple(
        value for item in claim.provider_readbacks for value in (item.initial_observed_at, item.final_observed_at)
    )
    return (
        claim.collection_started_at,
        claim.collection_completed_at,
        claim.seal.initial_read_at,
        claim.seal.final_read_at,
        *actions,
        *deliveries,
        *readbacks,
    )


def _actions(values: tuple[ServerActionClaim, ...]) -> ActionIndex:
    return {(item.release_kind, item.tool_name): item for item in values}


def _deliveries(values: tuple[ServerDeliveryClaim, ...]) -> DeliveryIndex:
    return {(item.release_kind, item.operation): item for item in values}


def _readbacks(values: tuple[ServerProviderReadback, ...]) -> ReadbackIndex:
    return {(item.release_kind, item.operation): item for item in values}


def _text(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _hashes(*values: str) -> bool:
    return all(isinstance(value, str) and _SHA256.fullmatch(value) is not None for value in values)


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
