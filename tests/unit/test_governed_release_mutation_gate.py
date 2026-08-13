"""Protected-runtime admission for Ontology and Pipeline release mutations."""

from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.primitives import _now
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.application.services.aip.governed_release_mutation_gate import release_execution_context
from foundry_lite.application.services.aip.governed_release_security_contract import (
    action_record,
    action_run_id,
    release_binding,
)
from foundry_lite.application.services.runtime_evidence_service import RuntimeEvidenceService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository, SqlAlchemyRuntimeRepository
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_DIRECT_ADMIN = RequestContext(actor_user_id="direct-admin", roles=("admin", "data_engineer"))


@pytest.mark.parametrize(
    ("operation", "resource_type", "resource_id"),
    (
        ("apply_ontology", "ontology", "draft"),
        ("rollback_ontology", "ontology", "rollback"),
        ("execute_ontology_proposal", "ontology_proposal", "ont-proposal-1"),
        ("execute_pipeline_proposal", "pipeline_proposal", "pipe-proposal-1"),
        ("deploy_pipeline_version", "pipeline_version", "pipe-version-1"),
    ),
)
def test_protected_profile_denies_direct_operational_mutations_and_audits(
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    service, _ai_repository, runtime_repository, engine = _service("production")

    with pytest.raises(PermissionDenied, match="Governed Release") as caught:
        service._require_write_traffic_open(
            _DIRECT_ADMIN,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    assert caught.value.details == {"reason": "governed_release_required", "operation": operation}
    with engine.begin() as conn:
        events = runtime_repository.rows_for_tenant(
            transaction=conn,
            table="audit_events",
            tenant_id=_DIRECT_ADMIN.tenant_id,
            event_types=["governed_release.mutation.denied"],
        )
    assert events[-1]["resource_id"] == resource_id
    assert events[-1]["decision"] == "deny"
    assert events[-1]["policy_decision"]["runtimeProfile"] == "production"


def test_local_profile_keeps_direct_iac_compatibility() -> None:
    service, _ai_repository, _runtime_repository, _engine = _service("test")

    for operation, resource_type, resource_id in (
        ("apply_ontology", "ontology", "draft"),
        ("rollback_ontology", "ontology", "rollback"),
        ("execute_pipeline_proposal", "pipeline_proposal", "proposal-local"),
        ("deploy_pipeline_version", "pipeline_version", "version-local"),
    ):
        service._require_write_traffic_open(
            _DIRECT_ADMIN,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
        )


def test_protected_profile_accepts_only_the_exact_running_action_attempt() -> None:
    service, ai_repository, _runtime_repository, engine = _service("staging")
    ctx, binding, run_id = _persist_action_run(
        ai_repository,
        engine,
        tool_name="execute_approved_release",
        release_kind="ontology",
        proposal_id="ont-proposal-1",
        required_permission="ontology:activate",
    )
    proof = release_execution_context(ctx, run_id, binding.fingerprint, binding.session_id, 0)

    service._require_write_traffic_open(
        proof,
        operation="execute_ontology_proposal",
        resource_type="ontology_proposal",
        resource_id="ont-proposal-1",
    )
    service._require_write_traffic_open(
        proof,
        operation="apply_ontology",
        resource_type="ontology",
        resource_id="draft",
    )

    for forged in (
        replace(proof, governed_release_execution_attempt=1),
        replace(proof, actor_user_id="other-actor"),
        replace(proof, client_id="other-client"),
        replace(proof, oauth_session_hash="oauth-session:sha256:other"),
        replace(proof, authorization_server_issuer="https://other-issuer.example.test"),
        replace(proof, oauth_resource="https://foundry.example.test/mcp/release/other-app"),
        replace(proof, oauth_token_expires_at=1_786_225_000),
        replace(proof, governed_release_binding_hash="sha256:forged"),
    ):
        with pytest.raises(PermissionDenied):
            service._require_write_traffic_open(
                forged,
                operation="execute_ontology_proposal",
                resource_type="ontology_proposal",
                resource_id="ont-proposal-1",
            )
    with pytest.raises(PermissionDenied):
        service._require_write_traffic_open(
            proof,
            operation="execute_ontology_proposal",
            resource_type="ontology_proposal",
            resource_id="different-proposal",
        )


@pytest.mark.parametrize("tool_name", ("deploy_release", "rollback_release"))
def test_pipeline_promotion_accepts_only_deploy_or_rollback_release_runs(tool_name: str) -> None:
    service, ai_repository, _runtime_repository, engine = _service("production")
    ctx, binding, run_id = _persist_action_run(
        ai_repository,
        engine,
        tool_name=tool_name,
        release_kind="pipeline",
        proposal_id="pipe-proposal-1",
        required_permission="pipeline:deploy",
    )
    proof = release_execution_context(ctx, run_id, binding.fingerprint, binding.session_id, 0)

    service._require_write_traffic_open(
        proof,
        operation="deploy_pipeline_version",
        resource_type="pipeline_version",
        resource_id="pipe-version-1",
    )


def _service(
    profile: str,
) -> tuple[RuntimeEvidenceService, SqlAlchemyAiRunRepository, SqlAlchemyRuntimeRepository, object]:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.create_database(engine)
    ai_repository = SqlAlchemyAiRunRepository(engine)
    runtime_repository = SqlAlchemyRuntimeRepository(engine)
    service = RuntimeEvidenceService(
        ai_run_repository=ai_repository,
        engine=engine,
        policy=PolicyService(allow_unwired_classification_provider=True),
        profile=RuntimeProfile.from_value(profile),
        runtime_repository=runtime_repository,
    )
    return service, ai_repository, runtime_repository, engine


def _persist_action_run(
    repository: SqlAlchemyAiRunRepository,
    engine: object,
    *,
    tool_name: str,
    release_kind: str,
    proposal_id: str,
    required_permission: str,
) -> tuple[RequestContext, object, str]:
    ctx = RequestContext(
        actor_user_id="release-reviewer",
        roles=("admin", "data_engineer"),
        application_id="release-app",
        client_id="release-client",
        token_scopes=("osdk:connector:governed_release:execute",),
        oauth_session_id="release-oauth-session",
        oauth_session_hash="oauth-session:sha256:release-oauth-session",
        oauth_session_authority="local",
        authorization_server_issuer="https://foundry-lite.local/osdk-oauth",
        oauth_grant_type="authorization_code",
        oauth_resource="https://foundry.example.test/mcp/release/release-app",
        oauth_token_issued_at=1_786_224_000,
        oauth_token_expires_at=1_786_224_900,
        is_human_oauth=True,
    )
    arguments = {
        "releaseKind": release_kind,
        "proposalId": proposal_id,
        "idempotencyKey": f"{tool_name}-idempotency",
    }
    binding = release_binding(
        ctx,
        application_id="release-app",
        session_id="mcp-release-admission-test",
        tool_name=tool_name,
        arguments=arguments,
        required_permission=required_permission,
        origin="https://chatgpt.com",
    )
    run_id = action_run_id(binding)
    with engine.begin() as conn:  # type: ignore[attr-defined]
        repository.insert_execution_run_or_get_existing(
            transaction=conn,
            record=action_record(ctx, binding, run_id, _now()),
        )
    return ctx, binding, run_id
