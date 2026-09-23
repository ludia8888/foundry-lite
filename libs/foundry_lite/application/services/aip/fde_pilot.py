"""Idempotent Pilot-style application bundle generation for AI FDE."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from foundry_lite.application.services.aip.fde_pilot_operating import (
    OntologyReleaseReader,
    active_application_coordinates,
    assigned_roles,
    is_role_mapping,
    operating_application_view,
    require_operating_application,
    require_operating_resource,
)
from foundry_lite.application.services.aip.fde_pilot_osdk_bundle import (
    ci_workflow,
    deployment_plan,
    react_files,
)
from foundry_lite.application.services.aip.fde_pilot_plan import (
    build_preview_pilot_plan,
    normalized_pilot_plan,
    pilot_identifier,
)
from foundry_lite.application.services.aip.fde_tool_result import (
    FdePlatformToolError,
    required_integer,
    required_text,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.ontology_branch_diff import parse_resource_map, serialize_resource_map
from foundry_lite.application.services.ontology_branch_service import OntologyBranchService
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.application.services.resource_catalog_service import ResourceCatalogService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]


class FdePilotService(CoreService):
    """Generate the governed resources required for a runnable OSDK React starter."""

    required_dependencies = ("engine", "dataset_transaction_repository")
    required_collaborators = (
        "dataset_ingest_service",
        "dataset_registry_service",
        "ontology_branch_service",
        "ontology_catalog_service",
        "osdk_application_service",
        "resource_catalog_service",
    )
    dataset_ingest_service: DatasetIngestService
    dataset_registry_service: DatasetRegistryService
    ontology_branch_service: OntologyBranchService
    ontology_catalog_service: OntologyReleaseReader
    osdk_application_service: OsdkApplicationService
    resource_catalog_service: ResourceCatalogService

    def plan(self, arguments: JsonObject) -> dict[str, object]:
        return build_preview_pilot_plan(arguments)

    def generate(
        self,
        ctx: RequestContext,
        plan: JsonObject,
        idempotency_key: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, object]:
        selected_project = self._project(ctx, plan, idempotency_key, project_id) if project_id is not None else None
        existing = self._existing_bundle(ctx, idempotency_key)
        if existing is not None:
            if project_id is not None and _mapping(existing.get("project"), "project").get("id") != project_id:
                raise ConflictDetected("pilot generation key belongs to another project")
            return self._replayed_application(ctx, existing, idempotency_key)
        normalized = normalized_pilot_plan(plan)
        project = selected_project or self._project(ctx, normalized, idempotency_key, None)
        seed = self._seed(ctx, normalized, idempotency_key)
        branch = self._ontology_branch(ctx, normalized, idempotency_key)
        application = self._application(ctx, normalized, idempotency_key)
        bundle = _bundle(normalized, project, seed, branch, application, idempotency_key)
        resource = self._register_pilot_resource(ctx, normalized, project, application, bundle, idempotency_key)
        role_mapping = self._ensure_creator_role_mapping(ctx, bundle, idempotency_key)
        workshop_resource = self._ensure_workshop_resource(ctx, bundle, idempotency_key)
        return {
            **bundle,
            "resource": resource["resource"],
            "roleMapping": role_mapping,
            "workshopResource": workshop_resource,
            "isReplayed": False,
        }

    def _register_pilot_resource(
        self,
        ctx: RequestContext,
        plan: JsonObject,
        project: JsonObject,
        application: JsonObject,
        bundle: JsonObject,
        key: str,
    ) -> dict[str, object]:
        application_record = _mapping(application.get("application"), "application")
        return self.resource_catalog_service.register_resource(
            resource_type="pilot_application",
            display_name=str(plan["applicationName"]),
            project_id=str(project["id"]),
            folder_id=None,
            source_surface="ai_fde_pilot",
            source_ref=str(application_record["id"]),
            operations_path=str(bundle["applicationPath"]),
            metadata=bundle,
            idempotency_key=f"{key}:pilot-resource",
            ctx=ctx,
        )

    def _replayed_application(self, ctx: RequestContext, bundle: JsonObject, idempotency_key: str) -> dict[str, object]:
        role_mapping = self._ensure_creator_role_mapping(ctx, bundle, idempotency_key)
        workshop_resource = self._ensure_workshop_resource(ctx, bundle, idempotency_key)
        return {
            **bundle,
            "roleMapping": role_mapping,
            "workshopResource": workshop_resource,
            "isReplayed": True,
        }

    def get_bundle(self, ctx: RequestContext, rid: str) -> dict[str, object]:
        payload = self.resource_catalog_service.get_resource(rid, ctx=ctx)
        resource = _mapping(payload.get("resource"), "resource")
        if resource.get("resourceType") != "pilot_application":
            raise FdePlatformToolError("resource_type_mismatch", "resource is not a Pilot application")
        metadata = _mapping(resource.get("metadata"), "resource.metadata")
        return {**metadata, "resource": resource}

    def find_business_system(self, ctx: RequestContext, application_id: str) -> dict[str, object] | None:
        """Find the shared definition owned by one tenant-visible consumer application."""

        resources = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        for item in _mapping_items(resources.get("items")):
            definition = _business_system_for_application(item, application_id)
            if definition is not None:
                return definition
        return None

    def get_operating_application(self, ctx: RequestContext, application_id: str) -> dict[str, object]:
        """Return one stable hosted app plus readiness from the active governed state."""

        bundle = self._bundle_for_application(ctx, application_id)
        application = self.osdk_application_service.get_application(application_id, ctx=ctx)
        catalog = self.ontology_catalog_service.release_active_catalog(ctx=ctx)
        role_mappings = self._role_mappings(ctx, application_id)
        operating = operating_application_view(
            bundle.get("businessSystemDefinition"), catalog, application, has_role_mapping=bool(role_mappings)
        )
        return {**bundle, "operatingApplication": operating}

    def operating_context(
        self,
        ctx: RequestContext,
        application_id: str,
        resource_kind: str,
        api_name: str,
    ) -> RequestContext:
        bundle = self.get_operating_application(ctx, application_id)
        operating = _mapping(bundle.get("operatingApplication"), "operatingApplication")
        require_operating_application(operating)
        require_operating_resource(bundle.get("businessSystemDefinition"), resource_kind, api_name)
        roles = assigned_roles(self._role_mappings(ctx, application_id), ctx.actor_user_id)
        application = self.osdk_application_service.get_application(application_id, ctx=ctx)
        client_id, scopes = active_application_coordinates(application)
        return replace(
            ctx,
            roles=tuple(sorted(set(ctx.roles) | roles)),
            application_id=application_id,
            client_id=client_id,
            token_scopes=scopes,
        )

    def _bundle_for_application(self, ctx: RequestContext, application_id: str) -> dict[str, object]:
        resources = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        for item in _mapping_items(resources.get("items")):
            metadata = _pilot_metadata_for_application(item, application_id)
            if metadata is not None:
                return {**metadata, "resource": item}
        raise FdePlatformToolError("pilot_application_not_found", "운영할 업무 앱을 찾지 못했습니다.")

    def _ensure_creator_role_mapping(
        self, ctx: RequestContext, bundle: JsonObject, idempotency_key: str
    ) -> dict[str, object]:
        blueprint = _mapping(bundle.get("domainOsBlueprint"), "domainOsBlueprint")
        roles = [str(item["role"]) for item in _mapping_items(blueprint.get("actorRoles"))]
        application = _mapping(bundle.get("osdkApplication"), "osdkApplication")
        app_record = _mapping(application.get("application"), "osdkApplication.application")
        project = _mapping(bundle.get("project"), "project")
        result = self.resource_catalog_service.register_resource(
            resource_type="business_application_role_mapping",
            display_name=f"{bundle['applicationName']} creator access",
            project_id=str(project["id"]),
            folder_id=None,
            source_surface="ai_fde_business_application_role_mapping",
            source_ref=f"{app_record['id']}:{ctx.actor_user_id}",
            operations_path=None,
            metadata={
                "applicationId": app_record["id"],
                "principalId": ctx.actor_user_id,
                "roles": roles,
                "mappingMode": "creator_preview",
            },
            idempotency_key=f"{idempotency_key}:creator-role-mapping",
            ctx=ctx,
        )
        return _mapping(result.get("resource"), "roleMapping.resource")

    def _role_mappings(self, ctx: RequestContext, application_id: str) -> list[dict[str, object]]:
        resources = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        return [item for item in _mapping_items(resources.get("items")) if is_role_mapping(item, application_id)]

    def _ensure_workshop_resource(
        self, ctx: RequestContext, bundle: JsonObject, idempotency_key: str
    ) -> dict[str, object]:
        application = _mapping(bundle.get("osdkApplication"), "osdkApplication")
        app_record = _mapping(application.get("application"), "osdkApplication.application")
        project = _mapping(bundle.get("project"), "project")
        definition = _mapping(bundle.get("businessSystemDefinition"), "businessSystemDefinition")
        experience = _mapping(definition.get("experience"), "businessSystemDefinition.experience")
        workshop_app = _mapping(experience.get("workshopApp"), "businessSystemDefinition.experience.workshopApp")
        app_id = str(app_record["id"])
        result = self.resource_catalog_service.register_resource(
            resource_type="workshop_app",
            display_name=str(bundle["applicationName"]),
            project_id=str(project["id"]),
            folder_id=None,
            source_surface="workshop",
            source_ref=app_id,
            operations_path=f"/workshop/{app_id}",
            metadata={
                "kind": "foundry-lite.workshop.app-definition",
                "schemaVersion": 2,
                "definition": workshop_app,
                "businessSystemDefinitionFingerprint": definition["definitionFingerprint"],
            },
            idempotency_key=f"{idempotency_key}:workshop-resource",
            ctx=ctx,
        )
        return _mapping(result.get("resource"), "workshop.resource")

    def _existing_bundle(self, ctx: RequestContext, key: str) -> dict[str, object] | None:
        resources = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        for item in _mapping_items(resources.get("items")):
            metadata = item.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("pilotIdempotencyKey") == key:
                return {str(name): value for name, value in metadata.items()}
        return None

    def _project(self, ctx: RequestContext, plan: JsonObject, key: str, project_id: str | None) -> dict[str, object]:
        if project_id is not None:
            result = self.resource_catalog_service.get_project(project_id, minimum_role="editor", ctx=ctx)
            project = _mapping(result.get("project"), "project")
            if project.get("status") != "active":
                raise ValidationFailed("pilot project must be active", details={"project_id": project_id})
            return project
        result = self.resource_catalog_service.create_project(
            display_name=str(plan["projectDisplayName"]),
            description=str(plan["domainDescription"]),
            metadata={"createdBy": "ai_fde_pilot", "slug": plan["slug"]},
            idempotency_key=f"{key}:project",
            ctx=ctx,
        )
        return _mapping(result.get("project"), "project")

    def _seed(self, ctx: RequestContext, plan: JsonObject, key: str) -> dict[str, object]:
        seed = _mapping(plan.get("seed"), "seed")
        datasets = _mapping_items(seed.get("datasets"))
        results = [self._seed_dataset(ctx, item, key) for item in datasets]
        primary = results[0]
        row_count = sum(required_integer(item.get("rowCount"), "seed.rowCount") for item in results)
        return {**primary, "datasets": results, "rowCount": row_count}

    def _seed_dataset(self, ctx: RequestContext, seed: JsonObject, key: str) -> dict[str, object]:
        dataset_ref = required_text(seed, "datasetRef")
        primary_key = _text_list(seed.get("primaryKey"), "seed.primaryKey")
        rows = _mapping_items(seed.get("rows"))
        self.dataset_registry_service.ensure_dataset(dataset_ref, primary_key=primary_key, ctx=ctx)
        replay = self._seed_replay(ctx, dataset_ref, key, len(rows))
        if replay is not None:
            return {**replay, "recordApiName": seed.get("recordApiName")}
        commit = self.dataset_ingest_service.sync_rows_batch(
            dataset_ref,
            rows,
            fieldnames=_fieldnames(rows),
            ctx=ctx,
            sync_name=f"ai-fde-pilot:{key}",
            tx_type="SNAPSHOT",
            source_type="ai_fde_pilot",
            transaction_metadata={"pilotIdempotencyKey": key},
        )
        return {
            "recordApiName": seed.get("recordApiName"),
            "datasetRef": dataset_ref,
            "rowCount": len(rows),
            "versionId": getattr(commit, "version_id", None),
        }

    def _seed_replay(self, ctx: RequestContext, dataset_ref: str, key: str, row_count: int) -> dict[str, object] | None:
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        with self.engine.begin() as conn:
            transaction = self.dataset_transaction_repository.latest_committed_transaction(
                transaction=conn, tenant_id=ctx.tenant_id, dataset_id=dataset["id"]
            )
        if transaction is None:
            return None
        metadata = transaction.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("pilotIdempotencyKey") != key:
            return None
        return {
            "datasetRef": dataset_ref,
            "rowCount": row_count,
            "versionId": transaction.get("committed_version_id"),
        }

    def _ontology_branch(self, ctx: RequestContext, plan: JsonObject, key: str) -> dict[str, object]:
        branch = self.ontology_branch_service.create_branch(
            name=f"pilot-{plan['slug']}", idempotency_key=f"{key}:ontology-branch", ctx=ctx
        )
        branch_id = str(branch["id"])
        detail = self.ontology_branch_service.get_branch(branch_id, ctx=ctx)
        resources = parse_resource_map(required_text(detail, "yamlText"))
        for entry in _mapping_items(plan.get("ontologyResources")):
            kind = required_text(entry, "kind")
            definition = _mapping(entry.get("definition"), "ontology resource definition")
            resources[(kind, required_text(definition, "apiName"))] = definition
        updated = self.ontology_branch_service.update_branch_content(
            branch_id,
            yaml_text=serialize_resource_map(resources),
            expected_fingerprint=required_text(detail, "contentFingerprint"),
            ctx=ctx,
        )
        return {
            "id": branch_id,
            "contentFingerprint": updated["contentFingerprint"],
            "diff": self.ontology_branch_service.branch_diff(branch_id, ctx=ctx),
        }

    def _application(self, ctx: RequestContext, plan: JsonObject, key: str) -> dict[str, object]:
        return dict(
            self.osdk_application_service.create_application(
                ctx=ctx,
                app_api_name=pilot_identifier(str(plan["slug"])),
                display_name=str(plan["applicationName"]),
                resources=_mapping_items(plan.get("applicationResources")),
                idempotency_key=f"{key}:osdk-app",
            )
        )


def _bundle(
    plan: JsonObject,
    project: JsonObject,
    seed: JsonObject,
    branch: JsonObject,
    application: JsonObject,
    key: str,
) -> dict[str, object]:
    slug = str(plan["slug"])
    files = react_files(plan)
    application_record = _mapping(application.get("application"), "application")
    return {
        "operationType": "pilot_application_bundle",
        "pilotIdempotencyKey": key,
        "applicationName": plan["applicationName"],
        "domainOsBlueprint": dict(_mapping(plan.get("domainOsBlueprint"), "domainOsBlueprint")),
        "businessSystemDefinition": dict(_mapping(plan.get("businessSystemDefinition"), "businessSystemDefinition")),
        "project": dict(project),
        "seed": dict(seed),
        "ontologyBranch": dict(branch),
        "osdkApplication": dict(application),
        "consumerOsdk": dict(_mapping(plan.get("consumerOsdk"), "consumerOsdk")),
        "reactFiles": files,
        "ciWorkflow": ci_workflow(),
        "deploymentPlan": deployment_plan(application, plan, files),
        "applicationPath": f"/projects/{project['id']}/pilot/{slug}",
        "operatingPath": f"/apps/{application_record['id']}",
        "status": "generated_on_branch",
        "nextStep": "예시 데이터로 확인한 뒤 Ontology를 검토·활성화하고 호스팅 배포를 승인하세요.",
    }


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _mapping_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    if not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    return [{str(name): field for name, field in item.items()} for item in value if isinstance(item, Mapping)]


def _text_list(value: object, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a string list")
    result = [item for item in value if isinstance(item, str) and item]
    if len(result) != len(value) or not result:
        raise FdePlatformToolError("schema_invalid", f"{field} must be a non-empty string list")
    return result


def _fieldnames(rows: list[dict[str, object]]) -> list[str]:
    names = sorted({name for row in rows for name in row})
    if not names:
        raise FdePlatformToolError("schema_invalid", "Pilot seed rows must include at least one field")
    return names


def _business_system_for_application(resource: JsonObject, application_id: str) -> dict[str, object] | None:
    metadata = _pilot_metadata_for_application(resource, application_id)
    if metadata is None:
        return None
    definition = metadata.get("businessSystemDefinition")
    return _mapping(definition, "businessSystemDefinition")


def _pilot_metadata_for_application(resource: JsonObject, application_id: str) -> dict[str, object] | None:
    metadata = resource.get("metadata")
    if resource.get("resourceType") != "pilot_application" or not isinstance(metadata, Mapping):
        return None
    application = metadata.get("osdkApplication")
    app_record = application.get("application") if isinstance(application, Mapping) else None
    if not isinstance(app_record, Mapping) or app_record.get("id") != application_id:
        return None
    return {str(name): value for name, value in metadata.items()}
