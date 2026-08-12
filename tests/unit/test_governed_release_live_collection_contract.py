from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
    ServerActionClaim,
    ServerCollectionSeal,
    ServerDeliveryClaim,
    ServerLoadedCollectionClaim,
    ServerProviderReadback,
    assess_server_loaded_collection,
)

_BASE_TIME = datetime(2026, 8, 9, tzinfo=UTC)
_SUBMITTER = "submitter"
_REVIEWER = "reviewer"
_ACTION_PLAN: tuple[tuple[ReleaseKind, str, int, int, str], ...] = (
    ("ontology", "publish_release_candidate", 10, 15, _SUBMITTER),
    ("ontology", "assign_release_reviewer", 20, 21, _REVIEWER),
    ("ontology", "submit_release_decision", 25, 26, _REVIEWER),
    ("ontology", "execute_approved_release", 30, 35, _REVIEWER),
    ("ontology", "rollback_release", 40, 41, _REVIEWER),
    ("pipeline", "publish_release_candidate", 50, 55, _SUBMITTER),
    ("pipeline", "assign_release_reviewer", 60, 61, _REVIEWER),
    ("pipeline", "submit_release_decision", 65, 66, _REVIEWER),
    ("pipeline", "execute_approved_release", 70, 75, _REVIEWER),
    ("pipeline", "deploy_release", 80, 85, _REVIEWER),
    ("pipeline", "rollback_release", 90, 95, _REVIEWER),
)


def _at(minutes: int) -> datetime:
    return _BASE_TIME + timedelta(minutes=minutes)


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _actions() -> tuple[ServerActionClaim, ...]:
    auth = _digest("issuer-audience-client-origin-scope-grant")
    return tuple(
        ServerActionClaim(
            release_kind=kind,
            proposal_id=f"{kind}-proposal",
            tool_name=tool,
            ai_run_id=f"ai-run-{kind}-{tool}",
            binding_hash=_digest(f"binding:{kind}:{tool}"),
            authorization_fingerprint=auth,
            actor_subject_hash=_subject(role),
            oauth_session_hash=_oauth_session(role),
            mcp_session_id=f"mcp-{role}",
            idempotency_key=f"idempotency-{kind}-{tool}",
            status="succeeded",
            started_at=_at(start),
            completed_at=_at(completed),
        )
        for kind, tool, start, completed, role in _ACTION_PLAN
    )


def _subject(role: str) -> str:
    return _digest(f"subject:{role}")


def _oauth_session(role: str) -> str:
    return _digest(f"oauth-session:{role}")


def _action_for(
    actions: tuple[ServerActionClaim, ...],
    kind: ReleaseKind,
    tool: str,
) -> ServerActionClaim:
    return next(item for item in actions if item.release_kind == kind and item.tool_name == tool)


def _delivery(
    actions: tuple[ServerActionClaim, ...],
    kind: ReleaseKind,
    operation: DeliveryOperation,
    parent_delivery_id: str | None,
    workflow_run_id: str,
    dispatch_minute: int,
    complete_minute: int,
) -> ServerDeliveryClaim:
    tool = {
        "source_publish": "publish_release_candidate",
        "source_merge": "execute_approved_release",
        "application_deploy": "deploy_release",
        "application_rollback": "rollback_release",
    }[operation]
    action = _action_for(actions, kind, tool)
    delivery_id = f"delivery-{kind}-{operation}"
    source_resource = "pull:17" if kind == "ontology" else "pull:18"
    deployment_resource = "dep-current-18" if operation == "application_deploy" else "dep-rollback-19"
    resource_id = source_resource if operation.startswith("source_") else deployment_resource
    return ServerDeliveryClaim(
        release_kind=kind,
        proposal_id=f"{kind}-proposal",
        operation=operation,
        delivery_id=delivery_id,
        workflow_run_id=workflow_run_id,
        parent_delivery_id=parent_delivery_id,
        provider="github" if operation.startswith("source_") else "render",
        provider_resource_id=resource_id,
        ai_run_id=action.ai_run_id,
        binding_hash=action.binding_hash,
        result_fingerprint=_digest(f"result:{delivery_id}"),
        status="landed",
        dispatch_started_at=_at(dispatch_minute),
        completed_at=_at(complete_minute),
    )


def _deliveries(actions: tuple[ServerActionClaim, ...]) -> tuple[ServerDeliveryClaim, ...]:
    ontology_root = _action_for(actions, "ontology", "publish_release_candidate").ai_run_id
    pipeline_root = _action_for(actions, "pipeline", "publish_release_candidate").ai_run_id
    ontology_publish = _delivery(actions, "ontology", "source_publish", None, ontology_root, 11, 14)
    ontology_merge = _delivery(actions, "ontology", "source_merge", ontology_publish.delivery_id, ontology_root, 31, 34)
    pipeline_publish = _delivery(actions, "pipeline", "source_publish", None, pipeline_root, 51, 54)
    pipeline_merge = _delivery(actions, "pipeline", "source_merge", pipeline_publish.delivery_id, pipeline_root, 71, 74)
    pipeline_deploy = _delivery(
        actions, "pipeline", "application_deploy", pipeline_merge.delivery_id, pipeline_root, 81, 84
    )
    pipeline_rollback = _delivery(
        actions,
        "pipeline",
        "application_rollback",
        pipeline_deploy.delivery_id,
        pipeline_root,
        91,
        94,
    )
    return (
        ontology_publish,
        ontology_merge,
        pipeline_publish,
        pipeline_merge,
        pipeline_deploy,
        pipeline_rollback,
    )


def _readbacks(deliveries: tuple[ServerDeliveryClaim, ...]) -> tuple[ServerProviderReadback, ...]:
    return tuple(
        ServerProviderReadback(
            release_kind=delivery.release_kind,
            operation=delivery.operation,
            delivery_id=delivery.delivery_id,
            provider=delivery.provider,
            provider_resource_id=delivery.provider_resource_id,
            ledger_result_fingerprint=delivery.result_fingerprint,
            initial_evidence_fingerprint=_digest(f"provider:{delivery.delivery_id}"),
            final_evidence_fingerprint=_digest(f"provider:{delivery.delivery_id}"),
            provider_request_ids=(f"initial:{delivery.delivery_id}", f"final:{delivery.delivery_id}"),
            is_exact_target=True,
            is_terminal_success=True,
            initial_observed_at=_at(102),
            final_observed_at=_at(103),
        )
        for delivery in deliveries
    )


def _claim() -> ServerLoadedCollectionClaim:
    actions = _actions()
    deliveries = _deliveries(actions)
    database_fingerprint = _digest("selected-postgresql-rows")
    target_fingerprint = _digest("server-target-configuration")
    return ServerLoadedCollectionClaim(
        authority="server_postgresql_provider_readback",
        collection_id="collection-1",
        tenant_id="tenant-1",
        golden_run_id="golden-run-1",
        application_id="release-app",
        database_system="postgresql",
        provider_readback_mode="concrete_network",
        authorization_policy_fingerprint=_digest("issuer-audience-client-origin-scope-grant"),
        submitter_subject_hash=_subject(_SUBMITTER),
        submitter_oauth_session_hash=_oauth_session(_SUBMITTER),
        reviewer_subject_hash=_subject(_REVIEWER),
        reviewer_oauth_session_hash=_oauth_session(_REVIEWER),
        is_authorization_code_human_grant=True,
        actions=actions,
        deliveries=deliveries,
        provider_readbacks=_readbacks(deliveries),
        seal=ServerCollectionSeal(
            collection_id="collection-1",
            tenant_id="tenant-1",
            application_id="release-app",
            golden_run_id="golden-run-1",
            initial_database_fingerprint=database_fingerprint,
            final_database_fingerprint=database_fingerprint,
            initial_target_configuration_fingerprint=target_fingerprint,
            final_target_configuration_fingerprint=target_fingerprint,
            initial_read_at=_at(101),
            final_read_at=_at(104),
        ),
        collection_started_at=_at(100),
        collection_completed_at=_at(105),
    )


def test_server_loaded_postgresql_and_provider_claim_is_attestation_eligible() -> None:
    result = assess_server_loaded_collection(_claim())

    assert result.is_authoritative is True
    assert result.is_chronologically_valid is True
    assert result.is_toctou_stable is True
    assert result.is_attestation_eligible is True
    assert result.blockers == ()
    assert not hasattr(result, "is_live_verified")


def test_caller_json_with_forged_live_flag_is_not_an_accepted_input() -> None:
    forged: object = {"is_live_verified": True, "status": "live_verified"}

    with pytest.raises(TypeError, match="server_loaded_collection_claim_required"):
        assess_server_loaded_collection(cast(ServerLoadedCollectionClaim, forged))


def test_local_or_caller_supplied_typed_claim_cannot_authorize_attestation() -> None:
    result = assess_server_loaded_collection(replace(_claim(), authority="local_or_caller_supplied"))

    assert result.is_authoritative is False
    assert result.is_attestation_eligible is False
    assert "server_collection_authority_required" in result.blockers


def test_same_human_and_oauth_session_can_satisfy_assigned_review_contract() -> None:
    claim = _claim()
    actions = tuple(
        replace(
            action,
            actor_subject_hash=claim.submitter_subject_hash,
            oauth_session_hash=claim.submitter_oauth_session_hash,
            mcp_session_id="mcp-submit-and-review",
        )
        for action in claim.actions
    )
    result = assess_server_loaded_collection(
        replace(
            claim,
            reviewer_subject_hash=claim.submitter_subject_hash,
            reviewer_oauth_session_hash=claim.submitter_oauth_session_hash,
            actions=actions,
        )
    )

    assert result.is_authoritative is True
    assert result.is_attestation_eligible is True
    assert result.blockers == ()


def test_missing_action_cannot_satisfy_authoritative_golden_set() -> None:
    claim = _claim()
    result = assess_server_loaded_collection(replace(claim, actions=claim.actions[:-1]))

    assert result.is_authoritative is False
    assert "golden_action_set_mismatch" in result.blockers


def test_delivery_must_match_the_exact_ai_run_and_binding() -> None:
    claim = _claim()
    changed = replace(claim.deliveries[1], ai_run_id="other-ai-run")
    result = assess_server_loaded_collection(
        replace(claim, deliveries=(claim.deliveries[0], changed, *claim.deliveries[2:]))
    )

    assert result.is_authoritative is False
    assert "delivery_action_binding_mismatch" in result.blockers


def test_pipeline_actions_must_follow_publish_review_execute_deploy_rollback_order() -> None:
    claim = _claim()
    deploy = next(item for item in claim.actions if item.tool_name == "deploy_release")
    changed = replace(deploy, started_at=_at(74), completed_at=_at(85))
    actions = tuple(changed if item is deploy else item for item in claim.actions)
    result = assess_server_loaded_collection(replace(claim, actions=actions))

    assert result.is_chronologically_valid is False
    assert "pipeline_action_order_invalid" in result.blockers


def test_delivery_dispatch_must_be_inside_its_action_window() -> None:
    claim = _claim()
    changed = replace(claim.deliveries[-1], dispatch_started_at=_at(89))
    result = assess_server_loaded_collection(replace(claim, deliveries=(*claim.deliveries[:-1], changed)))

    assert result.is_chronologically_valid is False
    assert "delivery_outside_action_window" in result.blockers


@pytest.mark.parametrize(
    ("field", "blocker"),
    [
        ("final_database_fingerprint", "database_snapshot_changed_during_collection"),
        ("final_target_configuration_fingerprint", "target_configuration_changed_during_collection"),
    ],
)
def test_final_server_seal_detects_database_or_target_toctou(field: str, blocker: str) -> None:
    claim = _claim()
    if field == "final_database_fingerprint":
        changed_seal = replace(claim.seal, final_database_fingerprint=_digest(f"changed:{field}"))
    else:
        changed_seal = replace(claim.seal, final_target_configuration_fingerprint=_digest(f"changed:{field}"))
    result = assess_server_loaded_collection(replace(claim, seal=changed_seal))

    assert result.is_toctou_stable is False
    assert result.is_attestation_eligible is False
    assert blocker in result.blockers


def test_provider_evidence_change_during_collection_is_fail_closed() -> None:
    claim = _claim()
    changed = replace(claim.provider_readbacks[0], final_evidence_fingerprint=_digest("changed-provider-state"))
    result = assess_server_loaded_collection(
        replace(claim, provider_readbacks=(changed, *claim.provider_readbacks[1:]))
    )

    assert result.is_toctou_stable is False
    assert "provider_evidence_changed_during_collection" in result.blockers


def test_provider_readback_must_be_bounded_by_the_database_seal() -> None:
    claim = _claim()
    changed = replace(claim.provider_readbacks[0], final_observed_at=_at(105))
    result = assess_server_loaded_collection(
        replace(claim, provider_readbacks=(changed, *claim.provider_readbacks[1:]))
    )

    assert result.is_chronologically_valid is False
    assert "provider_readback_outside_sealed_window" in result.blockers


def test_provider_readback_must_prove_exact_terminal_outcome() -> None:
    claim = _claim()
    changed = replace(claim.provider_readbacks[-1], is_terminal_success=False)
    result = assess_server_loaded_collection(
        replace(claim, provider_readbacks=(*claim.provider_readbacks[:-1], changed))
    )

    assert result.is_authoritative is False
    assert "provider_readback_outcome_unverified" in result.blockers
