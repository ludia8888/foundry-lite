"""Idempotent Pilot-style application bundle generation for AI FDE."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, required_text
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.ontology_branch_diff import parse_resource_map, serialize_resource_map
from foundry_lite.application.services.ontology_branch_service import OntologyBranchService
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.application.services.resource_catalog_service import ResourceCatalogService
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


class FdePilotService(CoreService):
    """Generate the governed resources required for a runnable OSDK React starter."""

    required_dependencies = ("engine", "dataset_transaction_repository")
    required_collaborators = (
        "dataset_ingest_service",
        "dataset_registry_service",
        "ontology_branch_service",
        "osdk_application_service",
        "resource_catalog_service",
    )
    dataset_ingest_service: DatasetIngestService
    dataset_registry_service: DatasetRegistryService
    ontology_branch_service: OntologyBranchService
    osdk_application_service: OsdkApplicationService
    resource_catalog_service: ResourceCatalogService

    def plan(self, arguments: JsonObject) -> dict[str, object]:
        app_name = required_text(arguments, "applicationName")
        description = required_text(arguments, "domainDescription")
        slug = _slug(app_name)
        identifier = _identifier(slug)
        object_name = _pascal(slug)
        return {
            "operationType": "pilot_generation_plan",
            "applicationName": app_name,
            "domainDescription": description,
            "slug": slug,
            "projectDisplayName": f"{app_name} Pilot",
            "seed": _seed_plan(identifier),
            "ontologyResources": [_object_type(object_name, identifier)],
            "applicationResources": [
                {
                    "resourceType": "object",
                    "resourceApiName": object_name,
                    "scopes": [f"osdk:object:{object_name}:read"],
                }
            ],
            "react": {"routes": ["/"], "objectTypes": [object_name], "framework": "react"},
            "ci": {"commands": ["pnpm typecheck", "pnpm test", "pnpm build"]},
            "requiredApprovals": ["pilot.application.generate"],
        }

    def generate(
        self,
        ctx: RequestContext,
        plan: JsonObject,
        idempotency_key: str,
    ) -> dict[str, object]:
        existing = self._existing_bundle(ctx, idempotency_key)
        if existing is not None:
            return {**existing, "isReplayed": True}
        normalized = _normalized_plan(plan)
        project = self._project(ctx, normalized, idempotency_key)
        seed = self._seed(ctx, normalized, idempotency_key)
        branch = self._ontology_branch(ctx, normalized, idempotency_key)
        application = self._application(ctx, normalized, idempotency_key)
        application_record = _mapping(application.get("application"), "application")
        bundle = _bundle(normalized, project, seed, branch, application, idempotency_key)
        resource = self.resource_catalog_service.register_resource(
            resource_type="pilot_application",
            display_name=str(normalized["applicationName"]),
            project_id=str(project["id"]),
            folder_id=None,
            source_surface="ai_fde_pilot",
            source_ref=str(application_record["id"]),
            operations_path=str(bundle["applicationPath"]),
            metadata=bundle,
            idempotency_key=f"{idempotency_key}:pilot-resource",
            ctx=ctx,
        )
        return {**bundle, "resource": resource["resource"], "isReplayed": False}

    def get_bundle(self, ctx: RequestContext, rid: str) -> dict[str, object]:
        payload = self.resource_catalog_service.get_resource(rid, ctx=ctx)
        resource = _mapping(payload.get("resource"), "resource")
        if resource.get("resourceType") != "pilot_application":
            raise FdePlatformToolError("resource_type_mismatch", "resource is not a Pilot application")
        metadata = _mapping(resource.get("metadata"), "resource.metadata")
        return {**metadata, "resource": resource}

    def _existing_bundle(self, ctx: RequestContext, key: str) -> dict[str, object] | None:
        resources = self.resource_catalog_service.list_resources(
            project_id=None, folder_id=None, include_trashed=False, ctx=ctx
        )
        for item in _mapping_items(resources.get("items")):
            metadata = item.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("pilotIdempotencyKey") == key:
                return {str(name): value for name, value in metadata.items()}
        return None

    def _project(self, ctx: RequestContext, plan: JsonObject, key: str) -> dict[str, object]:
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
        dataset_ref = required_text(seed, "datasetRef")
        primary_key = _text_list(seed.get("primaryKey"), "seed.primaryKey")
        rows = _mapping_items(seed.get("rows"))
        self.dataset_registry_service.ensure_dataset(dataset_ref, primary_key=primary_key, ctx=ctx)
        replay = self._seed_replay(ctx, dataset_ref, key, len(rows))
        if replay is not None:
            return replay
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
                app_api_name=_identifier(str(plan["slug"])),
                display_name=str(plan["applicationName"]),
                resources=_mapping_items(plan.get("applicationResources")),
                idempotency_key=f"{key}:osdk-app",
            )
        )


def _normalized_plan(plan: JsonObject) -> dict[str, object]:
    normalized = {str(name): value for name, value in plan.items()}
    app_name = required_text(normalized, "applicationName")
    normalized["domainDescription"] = required_text(normalized, "domainDescription")
    normalized["slug"] = _slug(str(normalized.get("slug") or app_name))
    normalized["projectDisplayName"] = str(normalized.get("projectDisplayName") or f"{app_name} Pilot")
    _mapping(normalized.get("seed"), "seed")
    _mapping_items(normalized.get("ontologyResources"))
    _mapping_items(normalized.get("applicationResources"))
    return normalized


def _bundle(
    plan: JsonObject,
    project: JsonObject,
    seed: JsonObject,
    branch: JsonObject,
    application: JsonObject,
    key: str,
) -> dict[str, object]:
    slug = str(plan["slug"])
    return {
        "operationType": "pilot_application_bundle",
        "pilotIdempotencyKey": key,
        "applicationName": plan["applicationName"],
        "project": dict(project),
        "seed": dict(seed),
        "ontologyBranch": dict(branch),
        "osdkApplication": dict(application),
        "reactFiles": _react_files(plan),
        "ciWorkflow": _ci_workflow(),
        "applicationPath": f"/projects/{project['id']}/pilot/{slug}",
        "status": "generated_on_branch",
        "nextStep": "Review and activate the Ontology proposal before using production data.",
    }


def _react_files(plan: JsonObject) -> dict[str, str]:
    descriptor = _osdk_object_descriptor(plan)
    title = json.dumps(str(plan["applicationName"]), ensure_ascii=True)
    return {
        "package.json": (
            '{"dependencies":{"@foundry-lite/sdk":"workspace:*","react":"^19.0.0"},'
            '"scripts":{"build":"vite build","test":"vitest run","typecheck":"tsc --noEmit"}}'
        ),
        "src/App.tsx": (
            "import { useEffect, useState } from 'react';\n"
            "import { useFoundryLiteOsdkClient } from '@foundry-lite/sdk/react';\n"
            f"const ObjectType = {descriptor} as const;\n"
            f"const title = {title};\n"
            "export default function App() { const osdk = useFoundryLiteOsdkClient(); "
            "const [items, setItems] = useState<unknown[]>([]); "
            "useEffect(() => { void osdk(ObjectType).fetchPage({ pageSize: 50 })"
            ".then((page) => setItems(page.data)); }, [osdk]); "
            "return <main><h1>{title}</h1><pre>{JSON.stringify(items, null, 2)}</pre></main>; }\n"
        ),
        "src/main.tsx": "import App from './App';\nexport { App };\n",
        "src/generated/ontology.ts": f"export const PilotObjectType = {descriptor} as const;\n",
    }


def _osdk_object_descriptor(plan: JsonObject) -> str:
    resources = _mapping_items(plan.get("ontologyResources"))
    entry = resources[0] if resources else {}
    definition = _mapping(entry.get("definition"), "ontology resource definition")
    properties = [required_text(item, "apiName") for item in _mapping_items(definition.get("properties"))]
    descriptor: dict[str, object] = {
        "kind": "object",
        "apiName": required_text(definition, "apiName"),
        "primaryKey": required_text(definition, "primaryKey"),
        "titleProperty": "name" if "name" in properties else None,
        "properties": properties,
        "propertyDatasources": {},
    }
    return json.dumps(descriptor, sort_keys=True)


def _ci_workflow() -> str:
    return (
        "steps:\n"
        "  - run: pnpm install --frozen-lockfile\n"
        "  - run: pnpm typecheck\n"
        "  - run: pnpm test\n"
        "  - run: pnpm build\n"
    )


def _seed_plan(slug: str) -> dict[str, object]:
    return {
        "datasetRef": f"seed.{slug}",
        "primaryKey": ["id"],
        "rows": [{"id": "sample-1", "name": "Sample"}],
    }


def _object_type(api_name: str, slug: str) -> dict[str, object]:
    return {
        "kind": "objectType",
        "definition": {
            "apiName": api_name,
            "primaryKey": "id",
            "backing": {"dataset": f"seed.{slug}"},
            "properties": [
                {"apiName": "id", "column": "id", "type": "string", "nullable": False, "indexed": True},
                {"apiName": "name", "column": "name", "type": "string"},
            ],
        },
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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise FdePlatformToolError("schema_invalid", "applicationName must contain letters or numbers")
    return slug[:64]


def _identifier(slug: str) -> str:
    value = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_")
    if not value or not value[0].isalpha():
        value = f"pilot_{value}"
    return value[:64]


def _pascal(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.split("-")) or "PilotObject"
