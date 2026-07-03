"""Ontology activation and YAML validation use-case service."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from foundry_lite.application.ports import (
    ActionParameterSchema,
    ActionTypeRecord,
    LinkTypeRecord,
    ObjectTypeRecord,
    ObjectTypeRow,
    OntologyApplyResult,
    OntologyValidationResult,
    OntologyVersionRecord,
    PropertyTypeRecord,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_migration import (
    OntologyMigrationPlan,
    plan_ontology_migration,
)
from foundry_lite.application.services.ontology_migration_types import object_type_serving_config
from foundry_lite.application.services.ontology_protocols import (
    OntologyDatasetRegistry,
    OntologyDatasetVersions,
    OntologyRuntimeBoundary,
    require_ontology_write_open,
)
from foundry_lite.application.services.ontology_validation import (
    ontology_validation_result,
    validate_ontology_definition,
    validate_persisted_link,
    validate_persisted_object_type,
)
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_parameter_schema,
    action_type_definition,
    link_type_backing,
    mapping_sequence,
    object_type_backing,
    optional_bool,
    optional_str,
    property_derivation,
    require_yaml_text_within_limit,
    required_str,
    schema_columns,
    yaml_object,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


class OntologyActivationService(CoreService):
    """Validate, import, and activate ontology YAML definitions."""

    required_dependencies = ("engine", "ontology_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_version_service",
        "runtime_service",
    )
    dataset_registry_service: OntologyDatasetRegistry
    dataset_version_service: OntologyDatasetVersions
    runtime_service: OntologyRuntimeBoundary

    def apply_ontology(
        self,
        yaml_path: str | Path,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyApplyResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", "ontology", "draft")
        require_ontology_write_open(self.runtime_service, ctx, "apply_ontology", "ontology", "draft")
        definition = self._load_ontology_definition(yaml_path)
        with self.engine.begin() as conn:
            return self._apply_loaded_ontology(conn, ctx, definition)

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
        definition = self._load_ontology_text(yaml_text)
        with self.engine.begin() as conn:
            return self._apply_loaded_ontology(conn, ctx, definition)

    def validate_yaml_text(
        self,
        yaml_text: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyValidationResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", "ontology", "draft")
        require_yaml_text_within_limit(yaml_text)
        definition = self._load_ontology_text(yaml_text)
        with self.engine.begin() as conn:
            validate_ontology_definition(conn, ctx, definition, self._dataset_columns_for_ref)
            migration_plan = self._candidate_migration_plan(conn, ctx, definition)
        return ontology_validation_result(definition, migration_plan)

    def _apply_loaded_ontology(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        definition: YamlObject,
    ) -> OntologyApplyResult:
        validate_ontology_definition(conn, ctx, definition, self._dataset_columns_for_ref)
        migration_plan = self._candidate_migration_plan(conn, ctx, definition)
        migration_plan.raise_if_blocked()
        ontology_version_id, version_number = self._create_draft_version(conn, ctx)
        object_map = self._import_object_types(conn, ctx, ontology_version_id, definition, migration_plan)
        self._import_link_types(conn, ctx, ontology_version_id, definition, object_map)
        self._import_action_types(conn, ctx, ontology_version_id, definition, object_map)
        self._validate_ontology(conn, ctx, ontology_version_id)
        self._activate_ontology_version(conn, ctx, ontology_version_id)
        self._record_ontology_activation(conn, ctx, ontology_version_id, version_number, migration_plan)
        return {
            "ontology_version_id": ontology_version_id,
            "version_number": version_number,
            "migration_plan": migration_plan.to_payload(),
        }

    def _load_ontology_definition(self, yaml_path: str | Path) -> YamlObject:
        definition: object = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        return yaml_object(definition, "ontology yaml")

    def _load_ontology_text(self, yaml_text: str) -> YamlObject:
        definition: object = yaml.safe_load(yaml_text)
        return yaml_object(definition, "ontology yaml")

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
            self._import_properties_for_object_type(conn, ctx, object_id, item)
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
                config=self._object_type_config(item, migration_plan, api_name),
            ),
        )
        return object_id

    def _object_type_config(
        self,
        item: YamlObject,
        migration_plan: OntologyMigrationPlan,
        api_name: str,
    ) -> dict[str, object]:
        # titleProperty rides in the existing config JSON so no schema migration
        # is needed; it coexists with the reindex serving-contract entries.
        config = object_type_serving_config(migration_plan, api_name)
        title_property = optional_str(item, "titleProperty")
        if title_property is not None:
            config["titleProperty"] = title_property
        return config

    def _import_properties_for_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_id: str,
        item: YamlObject,
    ) -> None:
        seen_properties: set[str] = set()
        for prop in mapping_sequence(item, "properties"):
            prop_api = required_str(prop, "apiName")
            if prop_api in seen_properties:
                raise ValidationFailed("duplicate property apiName", details={"property": prop_api})
            seen_properties.add(prop_api)
            self._insert_property_type(conn, ctx, object_id, prop)

    def _insert_property_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_id: str,
        prop: YamlObject,
    ) -> None:
        prop_api = required_str(prop, "apiName")
        source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
        if source is None:
            raise ValidationFailed("property source must be set", details={"property": prop_api})
        self.ontology_repository.insert_property_type(
            transaction=conn,
            record=PropertyTypeRecord(
                property_type_id=_new_id("ptype"),
                tenant_id=ctx.tenant_id,
                object_type_id=object_id,
                api_name=prop_api,
                display_name=optional_str(prop, "displayName", prop_api) or prop_api,
                data_type=required_str(prop, "type"),
                nullable=optional_bool(prop, "nullable", True),
                indexed=optional_bool(prop, "indexed", False),
                searchable=optional_bool(prop, "searchable", False),
                editable=optional_bool(prop, "editable", False),
                classification=optional_str(prop, "classification"),
                source=source,
                column_name=optional_str(prop, "column"),
                edit_policy=optional_str(prop, "editPolicy", "edit_only" if source == "edit_layer" else "source_wins")
                or "source_wins",
                derivation=property_derivation(prop),
            ),
        )

    def _import_link_types(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: YamlObject,
        object_map: dict[str, str],
    ) -> None:
        for item in mapping_sequence(definition, "linkTypes"):
            self._insert_link_type(conn, ctx, ontology_version_id, item, object_map)

    def _insert_link_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        item: YamlObject,
        object_map: dict[str, str],
    ) -> None:
        from_api = required_str(item, "from")
        to_api = required_str(item, "to")
        if from_api not in object_map or to_api not in object_map:
            raise ValidationFailed("link references unknown object type", details=dict(item))
        api_name = required_str(item, "apiName")
        self.ontology_repository.insert_link_type(
            transaction=conn,
            record=LinkTypeRecord(
                link_type_id=_new_id("ltype"),
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
                api_name=api_name,
                display_name=optional_str(item, "displayName", api_name) or api_name,
                from_object_type_id=object_map[from_api],
                from_api_name=from_api,
                to_object_type_id=object_map[to_api],
                to_api_name=to_api,
                cardinality=optional_str(item, "cardinality", "many_to_one") or "many_to_one",
                backing=link_type_backing(item),
            ),
        )

    def _import_action_types(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: YamlObject,
        object_map: dict[str, str],
    ) -> None:
        for item in mapping_sequence(definition, "actionTypes"):
            self._insert_action_type(conn, ctx, ontology_version_id, item, object_map)

    def _insert_action_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        item: YamlObject,
        object_map: dict[str, str],
    ) -> None:
        target = required_str(item, "target")
        if target not in object_map:
            raise ValidationFailed("action target object type not found", details=dict(item))
        parameters = mapping_sequence(item, "parameters")
        parameter_schema: ActionParameterSchema = action_parameter_schema(parameters)
        api_name = required_str(item, "apiName")
        self.ontology_repository.insert_action_type(
            transaction=conn,
            record=ActionTypeRecord(
                action_type_id=_new_id("atype"),
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
                api_name=api_name,
                display_name=optional_str(item, "displayName", api_name) or api_name,
                target_object_type_id=object_map[target],
                target_api_name=target,
                parameter_schema=parameter_schema,
                definition=action_type_definition(item),
                enabled=True,
            ),
        )

    def _validate_ontology(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> None:
        object_rows = self.ontology_repository.object_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )
        object_by_api = {row["api_name"]: row for row in object_rows}
        for object_type in object_rows:
            self._validate_ontology_object_type(conn, ctx, object_type)
        for link in self.ontology_repository.link_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        ):
            columns = self._dataset_columns_for_ref(conn, ctx, link["backing"]["dataset"])
            validate_persisted_link(link, object_by_api, columns)

    def _validate_ontology_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
    ) -> None:
        backing = object_type["backing"]
        if not isinstance(backing, Mapping):
            raise ValidationFailed("object backing must be a mapping")
        columns = self._dataset_columns_for_ref(conn, ctx, str(backing["dataset"]))
        properties = self.ontology_repository.properties_for_object_type(
            transaction=conn,
            object_type_id=object_type["id"],
        )
        actions = self.ontology_repository.actions_for_target(transaction=conn, object_type_id=object_type["id"])
        validate_persisted_object_type(object_type, properties, actions, columns)

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
