from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRecord,
    LinkTypeRecord,
    ObjectTypeRecord,
    OntologyVersionRecord,
    PropertyTypeRecord,
)
from foundry_lite.application.primitives import (
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    NotFound,
    ValidationFailed,
)


class OntologyServiceMixin(CoreServiceMixin):
    def apply_ontology(
        self,
        yaml_path: str | Path,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "ontology:activate", "ontology", "draft")
        definition = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        if not isinstance(definition, dict):
            raise ValidationFailed("ontology yaml must be a mapping")
        with self.engine.begin() as conn:
            version_number = self._next_ontology_version(conn, ctx)
            ontology_version_id = _new_id("ont")
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
            object_map = self._import_object_types(conn, ctx, ontology_version_id, definition)
            self._import_link_types(conn, ctx, ontology_version_id, definition, object_map)
            self._import_action_types(conn, ctx, ontology_version_id, definition, object_map)
            self._validate_ontology(conn, ctx, ontology_version_id)
            self.ontology_repository.archive_active_ontology_versions(transaction=conn, tenant_id=ctx.tenant_id)
            self.ontology_repository.activate_ontology_version(
                transaction=conn,
                ontology_version_id=ontology_version_id,
                activated_at=_now(),
            )
            self._outbox(
                conn,
                ctx,
                "ontology.version.activated",
                "ontology_version",
                ontology_version_id,
                {"ontologyVersionId": ontology_version_id},
                idempotency_key=ontology_version_id,
                correlation_id=ctx.request_id,
            )
            self._audit(
                conn,
                ctx,
                event_type="ontology.version.activated",
                resource_type="ontology_version",
                resource_id=ontology_version_id,
                action="activate",
                after_ref={"version_number": version_number},
            )
            return {"ontology_version_id": ontology_version_id, "version_number": version_number}

    def _next_ontology_version(self, conn: Any, ctx: RequestContext) -> int:
        return self.ontology_repository.next_ontology_version_number(transaction=conn, tenant_id=ctx.tenant_id)

    def _import_object_types(
        self,
        conn: Any,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: dict[str, Any],
    ) -> dict[str, str]:
        object_map: dict[str, str] = {}
        for item in definition.get("objectTypes", []):
            object_id = _new_id("otype")
            api_name = item["apiName"]
            self.ontology_repository.insert_object_type(
                transaction=conn,
                record=ObjectTypeRecord(
                    object_type_id=object_id,
                    tenant_id=ctx.tenant_id,
                    ontology_version_id=ontology_version_id,
                    api_name=api_name,
                    display_name=item.get("displayName", api_name),
                    description=item.get("description"),
                    primary_key_property=item["primaryKey"],
                    backing=item["backing"],
                    config={},
                ),
            )
            object_map[api_name] = object_id
            seen_properties: set[str] = set()
            for prop in item.get("properties", []):
                prop_api = prop["apiName"]
                if prop_api in seen_properties:
                    raise ValidationFailed("duplicate property apiName", details={"property": prop_api})
                seen_properties.add(prop_api)
                source = prop.get("source", "dataset" if "column" in prop else "edit_layer")
                self.ontology_repository.insert_property_type(
                    transaction=conn,
                    record=PropertyTypeRecord(
                        property_type_id=_new_id("ptype"),
                        tenant_id=ctx.tenant_id,
                        object_type_id=object_id,
                        api_name=prop_api,
                        display_name=prop.get("displayName", prop_api),
                        data_type=prop["type"],
                        nullable=prop.get("nullable", True),
                        indexed=prop.get("indexed", False),
                        searchable=prop.get("searchable", False),
                        editable=prop.get("editable", False),
                        classification=prop.get("classification"),
                        source=source,
                        column_name=prop.get("column"),
                        edit_policy=prop.get("editPolicy", "edit_only" if source == "edit_layer" else "source_wins"),
                        derivation=prop.get("derivation"),
                    ),
                )
        return object_map

    def _import_link_types(
        self,
        conn: Any,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: dict[str, Any],
        object_map: dict[str, str],
    ) -> None:
        for item in definition.get("linkTypes", []):
            if item["from"] not in object_map or item["to"] not in object_map:
                raise ValidationFailed("link references unknown object type", details=item)
            self.ontology_repository.insert_link_type(
                transaction=conn,
                record=LinkTypeRecord(
                    link_type_id=_new_id("ltype"),
                    tenant_id=ctx.tenant_id,
                    ontology_version_id=ontology_version_id,
                    api_name=item["apiName"],
                    display_name=item.get("displayName", item["apiName"]),
                    from_object_type_id=object_map[item["from"]],
                    from_api_name=item["from"],
                    to_object_type_id=object_map[item["to"]],
                    to_api_name=item["to"],
                    cardinality=item.get("cardinality", "many_to_one"),
                    backing=item["backing"],
                ),
            )

    def _import_action_types(
        self,
        conn: Any,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: dict[str, Any],
        object_map: dict[str, str],
    ) -> None:
        for item in definition.get("actionTypes", []):
            target = item["target"]
            if target not in object_map:
                raise ValidationFailed("action target object type not found", details=item)
            parameter_schema = {
                "type": "object",
                "required": [
                    parameter["apiName"] for parameter in item.get("parameters", []) if parameter.get("required", False)
                ],
                "properties": {
                    parameter["apiName"]: {"type": parameter["type"]} for parameter in item.get("parameters", [])
                },
            }
            self.ontology_repository.insert_action_type(
                transaction=conn,
                record=ActionTypeRecord(
                    action_type_id=_new_id("atype"),
                    tenant_id=ctx.tenant_id,
                    ontology_version_id=ontology_version_id,
                    api_name=item["apiName"],
                    display_name=item.get("displayName", item["apiName"]),
                    target_object_type_id=object_map[target],
                    target_api_name=target,
                    parameter_schema=parameter_schema,
                    definition=item,
                    enabled=True,
                ),
            )

    def _validate_ontology(self, conn: Any, ctx: RequestContext, ontology_version_id: str) -> None:
        object_rows = self._object_types_for_version(conn, ctx, ontology_version_id)
        object_by_api = {row["api_name"]: row for row in object_rows}
        for object_type in object_rows:
            self._validate_ontology_object_type(conn, ctx, object_type)
        for link in self._link_types_for_version(conn, ctx, ontology_version_id):
            self._validate_ontology_link(conn, ctx, link, object_by_api)

    def _validate_ontology_object_type(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type: dict[str, Any],
    ) -> None:
        columns = self._dataset_columns_for_ref(conn, ctx, object_type["backing"]["dataset"])
        properties = self._properties_for_object_type(conn, object_type["id"])
        property_by_api = {prop["api_name"]: prop for prop in properties}
        self._validate_primary_key_property(object_type, property_by_api, columns)
        self._validate_dataset_backed_properties(object_type, properties, columns)
        self._validate_ontology_action_mutations(conn, object_type, property_by_api)

    def _dataset_columns_for_ref(
        self,
        conn: Any,
        ctx: RequestContext,
        dataset_ref: str,
    ) -> dict[str, dict[str, Any]]:
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        latest_version = self._latest_version_by_dataset_id(conn, dataset["id"])
        schema = self._schema_for_version(dataset["id"], latest_version["schema_version"])["schema_json"]
        return {column["name"]: column for column in schema["columns"]}

    def _validate_primary_key_property(
        self,
        object_type: dict[str, Any],
        property_by_api: dict[str, dict[str, Any]],
        columns: dict[str, dict[str, Any]],
    ) -> None:
        pk_prop = object_type["primary_key_property"]
        if pk_prop not in property_by_api:
            raise ValidationFailed("primary key property missing", details={"objectType": object_type["api_name"]})
        pk_column = property_by_api[pk_prop]["column_name"]
        if pk_column not in columns:
            raise ValidationFailed("primary key column missing", details={"column": pk_column})
        if columns[pk_column]["nullable"]:
            raise ValidationFailed("primary key column must be non-null", details={"column": pk_column})

    def _validate_dataset_backed_properties(
        self,
        object_type: dict[str, Any],
        properties: list[dict[str, Any]],
        columns: dict[str, dict[str, Any]],
    ) -> None:
        for prop in properties:
            if prop["source"] == "dataset" and prop["column_name"] not in columns:
                raise ValidationFailed(
                    "property column missing",
                    details={"objectType": object_type["api_name"], "property": prop["api_name"]},
                )

    def _validate_ontology_action_mutations(
        self,
        conn: Any,
        object_type: dict[str, Any],
        property_by_api: dict[str, dict[str, Any]],
    ) -> None:
        for action in self._actions_for_target(conn, object_type["id"]):
            for mutation in action["definition"].get("mutations", []):
                self._validate_action_mutation_property(mutation, property_by_api)

    def _validate_action_mutation_property(
        self,
        mutation: dict[str, Any],
        property_by_api: dict[str, dict[str, Any]],
    ) -> None:
        prop = mutation.get("property")
        if prop not in property_by_api:
            raise ValidationFailed("action mutation property missing", details=mutation)
        if not property_by_api[prop]["editable"]:
            raise ValidationFailed("action mutation property must be editable", details=mutation)

    def _validate_ontology_link(
        self,
        conn: Any,
        ctx: RequestContext,
        link: dict[str, Any],
        object_by_api: dict[str, dict[str, Any]],
    ) -> None:
        if link["from_api_name"] not in object_by_api or link["to_api_name"] not in object_by_api:
            raise ValidationFailed("link object type missing", details={"linkType": link["api_name"]})
        columns = set(self._dataset_columns_for_ref(conn, ctx, link["backing"]["dataset"]))
        missing_keys = [key for key in [link["backing"]["fromKey"], link["backing"]["toKey"]] if key not in columns]
        if missing_keys:
            raise ValidationFailed(
                "link backing key missing",
                details={"linkType": link["api_name"], "missing": missing_keys},
            )

    def _object_types_for_version(
        self,
        conn: Any,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> list[dict[str, Any]]:
        return self.ontology_repository.object_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _link_types_for_version(
        self,
        conn: Any,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> list[dict[str, Any]]:
        return self.ontology_repository.link_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _properties_for_object_type(self, conn: Any, object_type_id: str) -> list[dict[str, Any]]:
        return self.ontology_repository.properties_for_object_type(transaction=conn, object_type_id=object_type_id)

    def _actions_for_target(self, conn: Any, object_type_id: str) -> list[dict[str, Any]]:
        return self.ontology_repository.actions_for_target(transaction=conn, object_type_id=object_type_id)

    def _active_ontology_version(self, conn: Any, ctx: RequestContext) -> dict[str, Any]:
        row = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
        if row is None:
            raise NotFound("active ontology not found")
        return row

    def _active_object_type(
        self,
        conn: Any,
        ctx: RequestContext,
        api_name: str,
    ) -> dict[str, Any]:
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
        conn: Any,
        ctx: RequestContext,
        api_name: str,
    ) -> dict[str, Any]:
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
