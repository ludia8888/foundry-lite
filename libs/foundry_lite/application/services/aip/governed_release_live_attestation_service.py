"""DB-backed readiness projection for authentic hosted release evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from foundry_lite.application.dependency_release import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationRecord,
    GovernedReleaseLiveAttestationRepository,
    GovernedReleaseLiveAuthority,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.governed_release_live_artifacts import (
    build_governed_release_live_artifacts,
)
from foundry_lite.application.services.aip.governed_release_live_attestation_writer import (
    LIVE_ATTESTATION_SCHEMA,
    LiveAttestationEvidenceBoundary,
    PreparedLiveAttestation,
    ServerVerifiedLiveCollection,
    live_attestation_evidence,
    prepare_live_attestation,
)
from foundry_lite.application.services.aip.governed_release_live_authority import (
    is_stored_live_replay_invoker,
    live_collection_digest,
    live_configuration_fingerprint,
)
from foundry_lite.application.services.aip.governed_release_live_collection import (
    ServerLoadedDatabaseSnapshot,
    build_server_loaded_collection_claim,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_loader import (
    GovernedReleaseLiveCollectionDatabaseLoader,
)
from foundry_lite.application.services.aip.governed_release_live_provider_collector import (
    GovernedReleaseLiveProviderCollector,
    LiveProviderSnapshot,
)
from foundry_lite.application.services.aip.governed_release_live_readiness_projection import (
    live_readiness_blocker,
    live_readiness_projection,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class GovernedReleaseLiveAttestationService(CoreService):
    """Trust only immutable rows written by the server-owned live collector."""

    required_dependencies = (
        "ai_run_repository",
        "engine",
        "governed_release_delivery_config",
        "governed_release_live_attestation_repository",
        "governed_release_live_authority",
        "infrastructure_deployment_adapter",
        "release_delivery_repository",
        "runtime_repository",
        "source_control_release_adapter",
    )
    required_collaborators = ("runtime_service",)
    governed_release_delivery_config: GovernedReleaseDeliveryConfig
    governed_release_live_attestation_repository: GovernedReleaseLiveAttestationRepository
    governed_release_live_authority: GovernedReleaseLiveAuthority
    runtime_service: LiveAttestationEvidenceBoundary

    def live_readiness(self, ctx: RequestContext, application_id: str) -> dict[str, object]:
        """Return current DB attestation state without reading operator files."""

        authority = self.governed_release_live_authority
        configuration_fingerprint = live_configuration_fingerprint(
            application_id,
            self.governed_release_delivery_config,
            authority,
        )
        with self.engine.begin() as transaction:
            row = self.governed_release_live_attestation_repository.latest_verified(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                application_id=application_id,
            )
        blocker = live_readiness_blocker(
            application_id,
            authority,
            row,
            configuration_fingerprint,
            datetime.now(UTC),
        )
        return live_readiness_projection(application_id, authority, row, blocker, configuration_fingerprint)

    def store_server_verified(
        self,
        ctx: RequestContext,
        collection: ServerVerifiedLiveCollection,
    ) -> dict[str, object]:
        """Persist one typed collector result with audit and outbox atomically."""

        fingerprint = live_configuration_fingerprint(
            collection.claim.application_id,
            self.governed_release_delivery_config,
            self.governed_release_live_authority,
        )
        prepared = prepare_live_attestation(ctx, collection, self.governed_release_live_authority, fingerprint)
        with self.engine.begin() as transaction:
            stored, is_created = self._store_prepared(transaction, ctx, prepared)
        return {**live_attestation_evidence(stored), "isCreated": is_created}

    def collect_and_store_server_verified(
        self,
        ctx: RequestContext,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> dict[str, object]:
        """Re-read the server DB and providers, then atomically seal live proof."""

        collection_id, golden_run_id, replay = self._collection_replay(
            ctx, application_id, ontology_workflow_run_id, pipeline_workflow_run_id
        )
        if replay is not None:
            return replay
        started_at = datetime.now(UTC)
        loader = self._collection_database_loader()
        initial_database = self._load_database(
            loader,
            ctx,
            application_id,
            ontology_workflow_run_id,
            pipeline_workflow_run_id,
        )
        provider = self._live_provider_collector()
        initial_provider = provider.read_once(ctx, initial_database.delivery_records, collection_id)
        final_provider = provider.read_once(ctx, initial_database.delivery_records, collection_id)
        return self._finalize_live_collection(
            ctx,
            application_id,
            ontology_workflow_run_id,
            pipeline_workflow_run_id,
            collection_id,
            golden_run_id,
            started_at,
            loader,
            initial_database,
            initial_provider,
            final_provider,
        )

    def _collection_replay(
        self,
        ctx: RequestContext,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> tuple[str, str, dict[str, object] | None]:
        if not self.governed_release_live_authority.is_live_eligible_for(application_id):
            raise ConflictDetected("authentic live collector is not eligible in this runtime")
        collection_id, golden_run_id = _collection_identities(
            ctx, application_id, ontology_workflow_run_id, pipeline_workflow_run_id
        )
        with self.engine.begin() as transaction:
            existing = self.governed_release_live_attestation_repository.get_by_collector_run(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                application_id=application_id,
                collector_run_id=collection_id,
            )
        if existing is None:
            return collection_id, golden_run_id, None
        self._require_valid_replay(ctx, existing, ontology_workflow_run_id, pipeline_workflow_run_id)
        return collection_id, golden_run_id, {**live_attestation_evidence(existing), "isCreated": False}

    def _require_valid_replay(
        self,
        ctx: RequestContext,
        record: GovernedReleaseLiveAttestationRecord,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> None:
        configuration = live_configuration_fingerprint(
            record.application_id,
            self.governed_release_delivery_config,
            self.governed_release_live_authority,
        )
        blocker = live_readiness_blocker(
            record.application_id,
            self.governed_release_live_authority,
            record,
            configuration,
            datetime.now(UTC),
        )
        expected = (ctx.actor_user_id, ontology_workflow_run_id, pipeline_workflow_run_id)
        observed = (record.created_by, record.ontology_workflow_run_id, record.pipeline_workflow_run_id)
        is_invoker = is_stored_live_replay_invoker(
            ctx,
            self.governed_release_live_authority,
            record.application_id,
            record.evidence_json,
        )
        if blocker is not None or observed != expected or not is_invoker:
            reason = blocker or (
                "live_collection_replay_scope_mismatch"
                if observed != expected
                else "live_collection_replay_oauth_authority_mismatch"
            )
            raise ConflictDetected(
                "stored live collection replay does not match the current fenced action",
                details={"reason": reason},
            )

    def _collection_database_loader(self) -> GovernedReleaseLiveCollectionDatabaseLoader:
        return GovernedReleaseLiveCollectionDatabaseLoader(
            self.release_delivery_repository,
            self.ai_run_repository,
            self.runtime_repository,
        )

    def _live_provider_collector(self) -> GovernedReleaseLiveProviderCollector:
        return GovernedReleaseLiveProviderCollector(
            self.governed_release_delivery_config,
            self.source_control_release_adapter,
            self.infrastructure_deployment_adapter,
        )

    def _load_database(
        self,
        loader: GovernedReleaseLiveCollectionDatabaseLoader,
        ctx: RequestContext,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> ServerLoadedDatabaseSnapshot:
        with self.engine.begin() as transaction:
            return loader.load(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                application_id=application_id,
                ontology_workflow_run_id=ontology_workflow_run_id,
                pipeline_workflow_run_id=pipeline_workflow_run_id,
            )

    def _finalize_live_collection(
        self,
        ctx: RequestContext,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
        collection_id: str,
        golden_run_id: str,
        started_at: datetime,
        loader: GovernedReleaseLiveCollectionDatabaseLoader,
        initial_database: ServerLoadedDatabaseSnapshot,
        initial_provider: LiveProviderSnapshot,
        final_provider: LiveProviderSnapshot,
    ) -> dict[str, object]:
        with self.engine.begin() as transaction:
            final_database = loader.load(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                application_id=application_id,
                ontology_workflow_run_id=ontology_workflow_run_id,
                pipeline_workflow_run_id=pipeline_workflow_run_id,
            )
            prepared = self._prepare_collected_attestation(
                ctx,
                initial_database,
                final_database,
                initial_provider,
                final_provider,
                collection_id,
                golden_run_id,
                started_at,
            )
            stored, is_created = self._store_prepared(transaction, ctx, prepared)
        return {**live_attestation_evidence(stored), "isCreated": is_created}

    def _prepare_collected_attestation(
        self,
        ctx: RequestContext,
        initial_database: ServerLoadedDatabaseSnapshot,
        final_database: ServerLoadedDatabaseSnapshot,
        initial_provider: LiveProviderSnapshot,
        final_provider: LiveProviderSnapshot,
        collection_id: str,
        golden_run_id: str,
        started_at: datetime,
    ) -> PreparedLiveAttestation:
        claim = build_server_loaded_collection_claim(
            initial_database=initial_database,
            final_database=final_database,
            initial_provider=initial_provider,
            final_provider=final_provider,
            collection_id=collection_id,
            golden_run_id=golden_run_id,
            collection_started_at=started_at,
            collection_completed_at=datetime.now(UTC),
        )
        artifacts = build_governed_release_live_artifacts(
            final_database,
            final_provider,
            self.governed_release_delivery_config,
            golden_run_id,
        )
        collection = ServerVerifiedLiveCollection(claim, artifacts.manifest, artifacts.evidence, artifacts.preflight)
        fingerprint = live_configuration_fingerprint(
            claim.application_id,
            self.governed_release_delivery_config,
            self.governed_release_live_authority,
        )
        return prepare_live_attestation(ctx, collection, self.governed_release_live_authority, fingerprint)

    def _store_prepared(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        prepared: PreparedLiveAttestation,
    ) -> tuple[GovernedReleaseLiveAttestationRecord, bool]:
        stored, is_created = self.governed_release_live_attestation_repository.store_verified(
            transaction=transaction,
            record=prepared.record,
        )
        if is_created:
            self._record_attestation_evidence(transaction, ctx, stored)
        return stored, is_created

    def _record_attestation_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        record: GovernedReleaseLiveAttestationRecord,
    ) -> None:
        evidence = live_attestation_evidence(record)
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="governed_release.live_attestation.verified",
            resource_type="governed_release_live_attestation",
            resource_id=record.attestation_id,
            action="store_server_verified_live_collection",
            after_ref=evidence,
            correlation_id=record.collector_run_id,
        )
        self.runtime_service._outbox(
            transaction,
            ctx,
            "governed_release.live_attestation.verified",
            "governed_release_live_attestation",
            record.attestation_id,
            evidence,
            idempotency_key=f"governed-release-live:{record.application_id}:{record.collector_run_id}",
            correlation_id=record.collector_run_id,
        )


def _collection_identities(
    ctx: RequestContext,
    application_id: str,
    ontology_workflow_run_id: str,
    pipeline_workflow_run_id: str,
) -> tuple[str, str]:
    run_id = ctx.governed_release_run_id
    binding_hash = ctx.governed_release_binding_hash
    valid = (
        isinstance(run_id, str)
        and run_id.startswith("governed_release_run_")
        and isinstance(binding_hash, str)
        and binding_hash.startswith("sha256:")
        and ctx.application_id == application_id
        and all(value.strip() for value in (application_id, ontology_workflow_run_id, pipeline_workflow_run_id))
        and ontology_workflow_run_id != pipeline_workflow_run_id
    )
    if not valid:
        raise ConflictDetected("server-fenced live collection identity is invalid")
    seed = live_collection_digest(
        {
            "tenantId": ctx.tenant_id,
            "applicationId": application_id,
            "governedReleaseRunId": run_id,
            "governedReleaseBindingHash": binding_hash,
            "ontologyWorkflowRunId": ontology_workflow_run_id,
            "pipelineWorkflowRunId": pipeline_workflow_run_id,
        }
    ).removeprefix("sha256:")
    golden = live_collection_digest({"collectorSeed": seed, "purpose": "golden-release"}).removeprefix("sha256:")
    return f"live-collection-{seed}", f"golden-release-{golden}"


__all__ = [
    "GovernedReleaseLiveAttestationService",
    "LIVE_ATTESTATION_SCHEMA",
    "live_configuration_fingerprint",
]
