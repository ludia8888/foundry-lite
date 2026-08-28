"""Ontology activation and YAML validation use-case service."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from foundry_lite.application.ports import (
    ObjectTypeRecord,
    OntologyApplyResult,
    OntologyValidationResult,
    OntologyVersionRecord,
    ResourceCatalogRepository,
    TransactionContext,
)
from foundry_lite.application.ports.ontology_definition_reader import (
    OntologyDefinitionRead,
    OntologyDefinitionReader,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.visual_builder import VisualBuilderService
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_activation_contracts import (
    import_action_types,
    import_function_types,
    import_interface_types,
    ontology_validation_result,
    validate_activation_contracts,
)
from foundry_lite.application.services.ontology_activation_imports import (
    import_link_types,
    import_properties_for_object_type,
    object_type_config,
)
from foundry_lite.application.services.ontology_activation_receipts import (
    activation_fingerprint,
    record_activation_receipt,
    replay_activation,
    required_activation_key,
)
from foundry_lite.application.services.ontology_migration import (
    OntologyMigrationPlan,
    plan_ontology_migration,
)
from foundry_lite.application.services.ontology_persisted_validation import validate_persisted_ontology
from foundry_lite.application.services.ontology_protocols import (
    OntologyDatasetRegistry,
    OntologyDatasetVersions,
    OntologyRuntimeBoundary,
    require_ontology_write_open,
    upsert_ontology_resource,
)
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    mapping_sequence,
    object_type_backing,
    optional_str,
    require_yaml_text_within_limit,
    required_str,
    schema_columns,
)
from foundry_lite.application.services.ontology_yaml_loading import load_ontology_yaml_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class OntologyActivationService(CoreService):
    """Validate, import, and activate ontology YAML definitions."""

    required_dependencies = (
        "engine",
        "media_repository",
        "ontology_definition_reader",
        "ontology_repository",
        "resource_catalog_repository",
    )
    required_collaborators = (
        "dataset_registry_service",
        "dataset_version_service",
        "runtime_service",
        "visual_builder_service",
    )
    dataset_registry_service: OntologyDatasetRegistry
    dataset_version_service: OntologyDatasetVersions
    runtime_service: OntologyRuntimeBoundary
    visual_builder_service: VisualBuilderService
    resource_catalog_repository: ResourceCatalogRepository
    ontology_definition_reader: OntologyDefinitionReader

    def apply_ontology(self, yaml_path: str | Path, *, ctx: RequestContext | None = None) -> OntologyApplyResult:
        """Apply ontology YAML from a file through the one text-apply path."""
        content = self.ontology_definition_reader.read_definition(OntologyDefinitionRead(str(yaml_path)))
        return self.apply_ontology_text(content.yaml_text, ctx=ctx)

    def apply_ontology_text(
        self,
        yaml_text: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyApplyResult:
        """Apply ontology YAML supplied as text (IaC over REST) via the same path as file apply."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", "ontology", "draft")
        require_ontology_write_open(self.runtime_service, ctx, "apply_ontology", "ontology", "draft")
        require_yaml_text_within_limit(yaml_text)
        definition = load_ontology_yaml_text(yaml_text)
        with self.engine.begin() as conn:
            return self._apply_loaded_ontology(conn, ctx, definition)

    def apply_ontology_text_once(
        self,
        yaml_text: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OntologyApplyResult:
        """Apply once under the activation lock and replay the committed receipt."""
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", "ontology", "draft")
        require_ontology_write_open(self.runtime_service, ctx, "apply_ontology", "ontology", "draft")
        require_yaml_text_within_limit(yaml_text)
        definition = load_ontology_yaml_text(yaml_text)
        receipt_key = required_activation_key(idempotency_key)
        fingerprint = activation_fingerprint(definition)
        with self.engine.begin() as conn:
            self.ontology_repository.lock_ontology_for_activation(transaction=conn, tenant_id=ctx.tenant_id)
            replay = replay_activation(self.runtime_service, conn, ctx, receipt_key, fingerprint)
            if replay is not None:
                return replay
            result = self._apply_loaded_ontology_after_lock(conn, ctx, definition)
            record_activation_receipt(self.runtime_service, conn, ctx, receipt_key, fingerprint, result)
            return result

    def validate_yaml_text(
        self,
        yaml_text: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyValidationResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", "ontology", "draft")
        require_yaml_text_within_limit(yaml_text)
        definition = load_ontology_yaml_text(yaml_text)
        with self.engine.begin() as conn:
            validate_activation_contracts(
                self.media_repository,
                conn,
                ctx,
                definition,
                self._dataset_columns_for_ref,
                self.visual_builder_service,
            )
            migration_plan = self._candidate_migration_plan(conn, ctx, definition)
        return ontology_validation_result(definition, migration_plan)

    def _apply_loaded_ontology(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        definition: YamlObject,
    ) -> OntologyApplyResult:
        self.ontology_repository.lock_ontology_for_activation(transaction=conn, tenant_id=ctx.tenant_id)
        return self._apply_loaded_ontology_after_lock(conn, ctx, definition)

    def _apply_loaded_ontology_after_lock(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        definition: YamlObject,
    ) -> OntologyApplyResult:
        functions = validate_activation_contracts(
            self.media_repository,
            conn,
            ctx,
            definition,
            self._dataset_columns_for_ref,
            self.visual_builder_service,
        )
        migration_plan = self._candidate_migration_plan(conn, ctx, definition)
        migration_plan.raise_if_blocked()
        ontology_version_id, version_number = self._create_draft_version(conn, ctx)
        import_interface_types(self.ontology_repository, conn, ctx, ontology_version_id, definition)
        import_function_types(self.ontology_repository, conn, ctx, ontology_version_id, functions)
        object_map = self._import_object_types(conn, ctx, ontology_version_id, definition, migration_plan)
        import_link_types(self.ontology_repository, conn, ctx, ontology_version_id, definition, object_map)
        import_action_types(self.ontology_repository, conn, ctx, ontology_version_id, definition, object_map)
        self._validate_ontology(conn, ctx, ontology_version_id)
        self._activate_ontology_version(conn, ctx, ontology_version_id)
        self._upsert_ontology_resource(conn, ctx, ontology_version_id, version_number)
        self._record_ontology_activation(conn, ctx, ontology_version_id, version_number, migration_plan)
        return {
            "ontology_version_id": ontology_version_id,
            "version_number": version_number,
            "migration_plan": migration_plan.to_payload(),
        }

    def _create_draft_version(self, conn: TransactionContext, ctx: RequestContext) -> tuple[str, int]:
        version_number = self.ontology_repository.next_ontology_version_number(
            transaction=conn,
            tenant_id=ctx.tenant_id,
        )
        ontology_version_id = _new_id("ont")
        self._insert_draft_ontology_version(conn, ctx, ontology_version_id, version_number)
        return ontology_version_id, version_number

    def _insert_draft_ontology_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        version_number: int,
    ) -> None:
        self.ontology_repository.insert_ontology_version(
            transaction=conn,
            record=OntologyVersionRecord(
                ontology_version_id=ontology_version_id,
                tenant_id=ctx.tenant_id,
                version_number=version_number,
                status="draft",
                created_by=ctx.actor_user_id,
                created_at=_now(),
                activated_at=None,
            ),
        )

    def _candidate_migration_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        definition: YamlObject,
    ) -> OntologyMigrationPlan:
        active = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
        active_id = active["id"] if active is not None else None
        return plan_ontology_migration(
            repository=self.ontology_repository,
            transaction=conn,
            ctx=ctx,
            active_ontology_version_id=active_id,
            definition=definition,
        )

    def _import_object_types(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: YamlObject,
        migration_plan: OntologyMigrationPlan,
    ) -> dict[str, str]:
        object_map: dict[str, str] = {}
        for item in mapping_sequence(definition, "objectTypes"):
            api_name = required_str(item, "apiName")
            object_id = self._insert_object_type(conn, ctx, ontology_version_id, item, migration_plan)
            object_map[api_name] = object_id
            import_properties_for_object_type(self.ontology_repository, conn, ctx, object_id, item)
        return object_map

    def _insert_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        item: YamlObject,
        migration_plan: OntologyMigrationPlan,
    ) -> str:
        object_id = _new_id("otype")
        api_name = required_str(item, "apiName")
        self.ontology_repository.insert_object_type(
            transaction=conn,
            record=ObjectTypeRecord(
                object_type_id=object_id,
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
                api_name=api_name,
                display_name=optional_str(item, "displayName", api_name) or api_name,
                description=optional_str(item, "description"),
                primary_key_property=required_str(item, "primaryKey"),
                backing=object_type_backing(item),
                config=object_type_config(item, migration_plan, api_name),
            ),
        )
        return object_id

    def _validate_ontology(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> None:
        validate_persisted_ontology(
            self.ontology_repository,
            conn,
            ctx,
            ontology_version_id,
            self._dataset_columns_for_ref,
        )

    def _dataset_columns_for_ref(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_ref: str,
    ) -> Mapping[str, Mapping[str, object]]:
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        latest_version = self.dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
        schema = self.dataset_version_service._schema_for_version(dataset["id"], latest_version["schema_version"])[
            "schema_json"
        ]
        return schema_columns(schema, dataset_ref)

    def _activate_ontology_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> None:
        self.ontology_repository.archive_active_ontology_versions(transaction=conn, tenant_id=ctx.tenant_id)
        activated = self.ontology_repository.activate_ontology_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
            activated_at=_now(),
        )
        if not activated:
            details: dict[str, object] = {"ontology_version_id": ontology_version_id}
            raise ConflictDetected("ontology version activation lost its draft state", details=details)

    def _record_ontology_activation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        version_number: int,
        migration_plan: OntologyMigrationPlan,
    ) -> None:
        outbox_payload: dict[str, object] = {"ontologyVersionId": ontology_version_id}
        audit_after_ref: dict[str, object] = {"version_number": version_number}
        if migration_plan.has_changes:
            migration_payload = migration_plan.to_payload()
            outbox_payload["ontologyMigration"] = migration_payload
            audit_after_ref["ontologyMigration"] = migration_payload
        self.runtime_service._outbox(
            conn,
            ctx,
            "ontology.version.activated",
            "ontology_version",
            ontology_version_id,
            outbox_payload,
            idempotency_key=ontology_version_id,
            correlation_id=ctx.request_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="ontology.version.activated",
            resource_type="ontology_version",
            resource_id=ontology_version_id,
            action="activate",
            after_ref=audit_after_ref,
        )

    def _upsert_ontology_resource(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        version_number: int,
    ) -> None:
        upsert_ontology_resource(
            conn=conn,
            ctx=ctx,
            repository=self.resource_catalog_repository,
            runtime_service=self.runtime_service,
            ontology_version_id=ontology_version_id,
            version_number=version_number,
            now=_now(),
        )
