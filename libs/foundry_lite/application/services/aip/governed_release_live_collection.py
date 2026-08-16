"""Compose one sealed typed claim from DB and concrete provider snapshots."""

from __future__ import annotations

from datetime import datetime

from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    ServerCollectionSeal,
    ServerLoadedCollectionClaim,
    ServerProviderReadback,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ServerLoadedDatabaseSnapshot,
)
from foundry_lite.application.services.aip.governed_release_live_provider_collector import (
    LiveProviderSnapshot,
    pair_provider_snapshots,
)
from foundry_lite.domain.errors import ConflictDetected


def build_server_loaded_collection_claim(
    *,
    initial_database: ServerLoadedDatabaseSnapshot,
    final_database: ServerLoadedDatabaseSnapshot,
    initial_provider: LiveProviderSnapshot,
    final_provider: LiveProviderSnapshot,
    collection_id: str,
    golden_run_id: str,
    collection_started_at: datetime,
    collection_completed_at: datetime,
) -> ServerLoadedCollectionClaim:
    """Bind two DB reads and two provider reads without accepting evidence JSON."""

    _require_scope(initial_database, final_database, collection_id, golden_run_id)
    seal = ServerCollectionSeal(
        collection_id=collection_id,
        tenant_id=initial_database.tenant_id,
        application_id=initial_database.application_id,
        golden_run_id=golden_run_id,
        initial_database_fingerprint=initial_database.initial_database_fingerprint,
        final_database_fingerprint=final_database.initial_database_fingerprint,
        initial_target_configuration_fingerprint=initial_provider.target_configuration.fingerprint,
        final_target_configuration_fingerprint=final_provider.target_configuration.fingerprint,
        initial_read_at=initial_database.initial_read_at,
        final_read_at=final_database.initial_read_at,
    )
    return _claim(
        final_database,
        pair_provider_snapshots(initial_provider, final_provider),
        seal,
        collection_id,
        golden_run_id,
        collection_started_at,
        collection_completed_at,
    )


def _claim(
    database: ServerLoadedDatabaseSnapshot,
    provider_readbacks: tuple[ServerProviderReadback, ...],
    seal: ServerCollectionSeal,
    collection_id: str,
    golden_run_id: str,
    collection_started_at: datetime,
    collection_completed_at: datetime,
) -> ServerLoadedCollectionClaim:
    source_provider, deployment_provider = _delivery_providers(database)
    return ServerLoadedCollectionClaim(
        authority="server_postgresql_provider_readback",
        collection_id=collection_id,
        tenant_id=database.tenant_id,
        golden_run_id=golden_run_id,
        application_id=database.application_id,
        database_system="postgresql",
        provider_readback_mode="concrete_network",
        source_provider_name=source_provider,
        deployment_provider_name=deployment_provider,
        authorization_policy_fingerprint=database.authorization_policy_fingerprint,
        submitter_subject_hash=database.submitter_subject_hash,
        submitter_oauth_session_hash=database.submitter_oauth_session_hash,
        reviewer_subject_hash=database.reviewer_subject_hash,
        reviewer_oauth_session_hash=database.reviewer_oauth_session_hash,
        is_authorization_code_human_grant=database.is_authorization_code_human_grant,
        actions=database.actions,
        deliveries=database.deliveries,
        provider_readbacks=provider_readbacks,
        seal=seal,
        collection_started_at=collection_started_at,
        collection_completed_at=collection_completed_at,
    )


def _delivery_providers(database: ServerLoadedDatabaseSnapshot) -> tuple[str, str]:
    source = {item.provider for item in database.deliveries if item.operation.startswith("source_")}
    deployment = {item.provider for item in database.deliveries if not item.operation.startswith("source_")}
    if len(source) != 1 or len(deployment) != 1:
        raise ConflictDetected("live collection delivery provider identities are inconsistent")
    return source.pop(), deployment.pop()


def _require_scope(
    initial: ServerLoadedDatabaseSnapshot,
    final: ServerLoadedDatabaseSnapshot,
    collection_id: str,
    golden_run_id: str,
) -> None:
    initial_scope = (
        initial.tenant_id,
        initial.application_id,
        initial.ontology_workflow_run_id,
        initial.pipeline_workflow_run_id,
    )
    final_scope = (
        final.tenant_id,
        final.application_id,
        final.ontology_workflow_run_id,
        final.pipeline_workflow_run_id,
    )
    if initial_scope != final_scope:
        raise ConflictDetected("live collection database scope changed during provider readback")
    if not collection_id.strip() or not golden_run_id.strip():
        raise ValueError("server collection identities are required")


__all__ = ["ServerLoadedDatabaseSnapshot", "build_server_loaded_collection_claim"]
