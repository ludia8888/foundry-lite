from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ActionParameterSchema,
    ActionTypeRecord,
    ActionTypeRow,
    LinkTypeRecord,
    LinkTypeRow,
    ObjectTypeRecord,
    ObjectTypeRow,
    OntologyApplyResult,
    OntologyCatalogResult,
    OntologyValidationResult,
    OntologyVersionRecord,
    OntologyVersionRow,
    PropertyTypeRecord,
    PropertyTypeRow,
)
from foundry_lite.application.primitives import (
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_migration import (
    OntologyMigrationPlan,
    plan_ontology_migration,
)
from foundry_lite.application.services.ontology_protocols import (
    OntologyDatasetRegistry,
    OntologyDatasetVersions,
    OntologyRuntimeBoundary,
    require_ontology_write_open,
)
from foundry_lite.application.services.ontology_validation import (
    build_ontology_catalog,
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
    required_str,
    schema_columns,
    yaml_object,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    NotFound,
    ValidationFailed,
)


class OntologyService(CoreService):
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
            validate_ontology_definition(conn, ctx, definition, self._dataset_columns_for_ref)
            migration_plan = self._candidate_migration_plan(conn, ctx, definition)
            migration_plan.raise_if_blocked()
            version_number = self._next_ontology_version(conn, ctx)
            ontology_version_id = _new_id("ont")
            self._insert_draft_ontology_version(conn, ctx, ontology_version_id, version_number)
            object_map = self._import_object_types(conn, ctx, ontology_version_id, definition)
            self._import_link_types(conn, ctx, ontology_version_id, definition, object_map)
            self._import_action_types(conn, ctx, ontology_version_id, definition, object_map)
            self._validate_ontology(conn, ctx, ontology_version_id)
            self._activate_ontology_version(conn, ctx, ontology_version_id)
            self._record_ontology_activation(conn, ctx, ontology_version_id, version_number, migration_plan)
            return {"ontology_version_id": ontology_version_id, "version_number": version_number}

    def validate_yaml_text(
        self,
        yaml_text: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyValidationResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", "ontology", "draft")
        definition = self._load_ontology_text(yaml_text)
        with self.engine.begin() as conn:
            validate_ontology_definition(conn, ctx, definition, self._dataset_columns_for_ref)
        return ontology_validation_result(definition)

    def active_catalog(self, *, ctx: RequestContext | None = None) -> OntologyCatalogResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:read", "ontology", "active")
        with self.engine.begin() as conn:
            active = self._active_ontology_version(conn, ctx)
            object_rows = self._object_types_for_version(conn, ctx, active["id"])
            return build_ontology_catalog(
                active=active,
                object_rows=object_rows,
                link_rows=self._link_types_for_version(conn, ctx, active["id"]),
                properties_by_object_id={
                    item["id"]: self._properties_for_object_type(conn, item["id"]) for item in object_rows
                },
                actions_by_object_id={item["id"]: self._actions_for_target(conn, item["id"]) for item in object_rows},
            )

    def _load_ontology_definition(self, yaml_path: str | Path) -> YamlObject:
        """Load and validate the top-level ontology YAML mapping."""
        definition: object = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        return yaml_object(definition, "ontology yaml")

    def _load_ontology_text(self, yaml_text: str) -> YamlObject:
        definition: object = yaml.safe_load(yaml_text)
        return yaml_object(definition, "ontology yaml")

    def _insert_draft_ontology_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        version_number: int,
    ) -> None:
        """Persist the draft ontology version before importing its type rows."""
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

    def _record_ontology_activation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        version_number: int,
        migration_plan: OntologyMigrationPlan,
    ) -> None:
        """Write the durable activation outbox event and audit record."""
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

    def _activate_ontology_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> None:
        """Archive the old active ontology and activate the new tenant version."""
        self.ontology_repository.archive_active_ontology_versions(transaction=conn, tenant_id=ctx.tenant_id)
        self.ontology_repository.activate_ontology_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
            activated_at=_now(),
        )

    def _next_ontology_version(self, conn: TransactionContext, ctx: RequestContext) -> int:
        return self.ontology_repository.next_ontology_version_number(transaction=conn, tenant_id=ctx.tenant_id)

    def _import_object_types(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: YamlObject,
    ) -> dict[str, str]:
        """Import object types and return their API-name to row-id map."""
        object_map: dict[str, str] = {}
        for item in mapping_sequence(definition, "objectTypes"):
            api_name = required_str(item, "apiName")
            object_id = self._insert_object_type(conn, ctx, ontology_version_id, item)
            object_map[api_name] = object_id
            self._import_properties_for_object_type(conn, ctx, object_id, item)
        return object_map

    def _insert_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        item: YamlObject,
    ) -> str:
        """Persist one ontology object type row and return its generated id."""
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
                config={},
            ),
        )
        return object_id

    def _import_properties_for_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_id: str,
        item: YamlObject,
    ) -> None:
        """Import all properties for one object type, rejecting duplicates."""
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
        """Persist one property type row from its YAML declaration."""
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
        object_rows = self._object_types_for_version(conn, ctx, ontology_version_id)
        object_by_api = {row["api_name"]: row for row in object_rows}
        for object_type in object_rows:
            self._validate_ontology_object_type(conn, ctx, object_type)
        for link in self._link_types_for_version(conn, ctx, ontology_version_id):
            columns = self._dataset_columns_for_ref(conn, ctx, link["backing"]["dataset"])
            validate_persisted_link(link, object_by_api, columns)

    def _validate_ontology_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type: ObjectTypeRow,
    ) -> None:
        columns = self._dataset_columns_for_ref(conn, ctx, object_type["backing"]["dataset"])
        properties = self._properties_for_object_type(conn, object_type["id"])
        actions = self._actions_for_target(conn, object_type["id"])
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

    def _object_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[ObjectTypeRow]:
        return self.ontology_repository.object_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _link_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[LinkTypeRow]:
        return self.ontology_repository.link_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _properties_for_object_type(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> Sequence[PropertyTypeRow]:
        return self.ontology_repository.properties_for_object_type(transaction=conn, object_type_id=object_type_id)

    def _actions_for_target(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> Sequence[ActionTypeRow]:
        return self.ontology_repository.actions_for_target(transaction=conn, object_type_id=object_type_id)

    def _active_ontology_version(self, conn: TransactionContext, ctx: RequestContext) -> OntologyVersionRow:
        row = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
        if row is None:
            raise NotFound("active ontology not found")
        return row

    def _active_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ObjectTypeRow:
        active = self._active_ontology_version(conn, ctx)
        row = self.ontology_repository.object_type_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            api_name=api_name,
        )
        if row is None:
            raise NotFound("object type not found", details={"api_name": api_name})
        return row

    def _active_action_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ActionTypeRow:
        active = self._active_ontology_version(conn, ctx)
        row = self.ontology_repository.enabled_action_type_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            api_name=api_name,
        )
        if row is None:
            raise NotFound("action type not found", details={"api_name": api_name})
        return row
