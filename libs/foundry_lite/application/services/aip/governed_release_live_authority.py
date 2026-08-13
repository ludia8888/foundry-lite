"""Match live collection evidence to the current hosted OAuth authority."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.dependency_release import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAuthority,
)
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    ServerLoadedCollectionClaim,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import canonical_digest
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


def is_exact_live_reviewer_invoker(
    ctx: RequestContext,
    claim: ServerLoadedCollectionClaim,
    authority: GovernedReleaseLiveAuthority,
) -> bool:
    """Require the sealed reviewer and the current startup OAuth authority."""

    return is_exact_live_reviewer_identity(
        ctx,
        authority,
        claim.application_id,
        claim.reviewer_subject_hash,
        claim.reviewer_oauth_session_hash,
    )


def is_exact_live_reviewer_identity(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    application_id: str,
    reviewer_subject_hash: str,
    reviewer_oauth_session_hash: str,
) -> bool:
    """Match a server-loaded reviewer identity to the current OAuth request."""

    if not _current_request_matches(ctx, authority, application_id):
        return False
    return _request_reviewer_identity(ctx) == (reviewer_subject_hash, reviewer_oauth_session_hash)


def live_artifact_authority_blockers(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    application_id: str,
    manifest: JsonObject,
    evidence: JsonObject,
) -> list[str]:
    """Compare persisted artifacts with current server and request OAuth facts."""

    blockers: list[str] = []
    if not _current_request_matches(ctx, authority, application_id):
        blockers.append("current_oauth_authority_mismatch")
    if not _manifest_matches(ctx, authority, application_id, manifest):
        blockers.append("artifact_startup_oauth_authority_mismatch")
    if not _evidence_principals_match(ctx, authority, application_id, evidence):
        blockers.append("artifact_current_oauth_principal_mismatch")
    return blockers


def is_stored_live_replay_invoker(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    application_id: str,
    stored_evidence: JsonObject,
) -> bool:
    """Prevent a committed attestation replay through a different OAuth client."""

    manifest = _mapping(stored_evidence.get("manifest"))
    evidence = _mapping(stored_evidence.get("evidence"))
    if live_artifact_authority_blockers(ctx, authority, application_id, manifest, evidence):
        return False
    reviewer = _mapping(_mapping(evidence.get("principals")).get("reviewer"))
    return _request_reviewer_identity(ctx) == (
        reviewer.get("subjectHash"),
        reviewer.get("oauthSessionHash"),
    )


def live_mcp_configuration_payload(
    application_id: str,
    authority: GovernedReleaseLiveAuthority,
) -> dict[str, object]:
    """Return the startup OAuth facts that invalidate stale attestations."""

    mcp = authority.mcp_authority
    return {
        "configuredApplicationId": mcp.application_id,
        "publicMcpBaseUrl": mcp.public_base_url,
        "authorizationServerIssuer": mcp.authorization_server_issuer,
        "oauthTokenAudience": mcp.oauth_audience,
        "allowedOauthClientIds": list(mcp.allowed_client_ids),
        "requiredOauthScope": mcp.required_scope,
        "trustedOrigin": mcp.trusted_origin,
        "releaseOauthResource": mcp.release_resource(application_id),
    }


def live_configuration_fingerprint(
    application_id: str,
    config: GovernedReleaseDeliveryConfig,
    authority: GovernedReleaseLiveAuthority,
) -> str:
    """Bind a live attestation to the current delivery and OAuth startup facts."""

    repository = config.source_repository
    payload: dict[str, object] = {
        "applicationId": application_id,
        "sourceBaseRef": config.source_base_ref,
        "sourceHeadPrefix": config.source_head_prefix,
        "sourceMergeMethod": config.source_merge_method.value,
        "isSourceControlRequired": config.is_source_control_required,
        "deploymentServiceId": config.deployment_service_id,
        "deploymentEnvironment": config.deployment_environment,
        "isDeploymentRequired": config.is_deployment_required,
        "runtimeProfile": authority.runtime_profile,
        "databaseBackend": authority.database_backend,
        "sourceProviderProfile": authority.source_provider_profile,
        "deploymentProviderProfile": authority.deployment_provider_profile,
        "sourceRevision": authority.source_revision,
        **live_mcp_configuration_payload(application_id, authority),
    }
    if repository is not None:
        payload["sourceRepository"] = {
            "provider": repository.provider,
            "repositoryId": repository.repository_id,
            "owner": repository.owner,
            "name": repository.name,
        }
    return canonical_digest(payload)


def live_collection_digest(payload: JsonObject) -> str:
    """Hash one collector identity payload through the shared canonical encoder."""

    return canonical_digest(payload)


def _current_request_matches(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    application_id: str,
) -> bool:
    mcp = authority.mcp_authority
    return bool(
        authority.is_live_eligible_for(application_id)
        and ctx.application_id == application_id
        and ctx.authorization_server_issuer == mcp.authorization_server_issuer
        and ctx.oauth_resource == mcp.release_resource(application_id)
        and ctx.client_id in mcp.allowed_client_ids
        and mcp.required_scope in ctx.token_scopes
        and ctx.oauth_session_authority == "issuer"
        and ctx.oauth_grant_type == "authorization_code"
        and ctx.is_human_oauth is True
    )


def _manifest_matches(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    application_id: str,
    manifest: JsonObject,
) -> bool:
    mcp = authority.mcp_authority
    expected = {
        "applicationId": application_id,
        "publicBaseUrl": mcp.public_base_url,
        "authorizationServer": mcp.authorization_server_issuer,
        "hostedClientId": ctx.client_id,
        "requiredScope": mcp.required_scope,
        "resource": mcp.release_resource(application_id),
    }
    return all(manifest.get(key) == value for key, value in expected.items())


def _evidence_principals_match(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    application_id: str,
    evidence: JsonObject,
) -> bool:
    mcp = authority.mcp_authority
    expected = {
        "applicationId": application_id,
        "clientId": ctx.client_id,
        "issuer": mcp.authorization_server_issuer,
        "audience": mcp.release_resource(application_id),
        "grantType": "authorization_code",
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
    }
    principals = _mapping(evidence.get("principals"))
    capture = _mapping(evidence.get("capture"))
    actors = (_mapping(principals.get("submitter")), _mapping(principals.get("reviewer")))
    return capture.get("publicEndpoint") == expected["audience"] and all(
        all(actor.get(key) == value for key, value in expected.items()) for actor in actors
    )


def _request_reviewer_identity(ctx: RequestContext) -> tuple[object, object]:
    issuer = ctx.authorization_server_issuer
    session_hash = ctx.oauth_session_hash
    if not isinstance(issuer, str) or not isinstance(session_hash, str):
        return None, None
    subject_hash = hash_json({"issuer": issuer.rstrip("/"), "subject": ctx.actor_user_id})
    return subject_hash, session_hash.removeprefix("oauth-session:")


def _mapping(value: object) -> JsonObject:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "is_exact_live_reviewer_identity",
    "is_exact_live_reviewer_invoker",
    "is_stored_live_replay_invoker",
    "live_artifact_authority_blockers",
    "live_collection_digest",
    "live_configuration_fingerprint",
    "live_mcp_configuration_payload",
]
