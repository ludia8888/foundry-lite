"""Explicit, permission-checked resource attachments for AI FDE turns."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports.context_provider import RetrievedContextItem
from foundry_lite.application.services.aip.fde_catalog import current_fde_mode
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_branch_diff import parse_resource_map
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]
_MAX_ATTACHMENT_CHARACTERS = 4000


class FdeBranchReader(Protocol):
    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdeDatasetReader(Protocol):
    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdeOntologyCatalogReader(Protocol):
    def active_catalog(self, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdeOsdkApplicationReader(Protocol):
    def get_application(self, application_id: str, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdePipelineCatalogReader(Protocol):
    def trained_models(self, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdePipelineDefinitionReader(Protocol):
    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class FdeResourceCatalogReader(Protocol):
    def get_project(self, project_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def get_resource(self, rid: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class FdeSourceReader(Protocol):
    def get_source(self, source_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class FdeContextService(CoreService):
    """Resolve explicit platform resources through their normal permission checks."""

    required_dependencies = ()
    required_collaborators = (
        "dataset_registry_service",
        "ontology_branch_service",
        "ontology_catalog_service",
        "osdk_application_service",
        "pipeline_catalog_service",
        "pipeline_definition_service",
        "resource_catalog_service",
        "source_onboarding_service",
    )
    dataset_registry_service: FdeDatasetReader
    ontology_branch_service: FdeBranchReader
    ontology_catalog_service: FdeOntologyCatalogReader
    osdk_application_service: FdeOsdkApplicationReader
    pipeline_catalog_service: FdePipelineCatalogReader
    pipeline_definition_service: FdePipelineDefinitionReader
    resource_catalog_service: FdeResourceCatalogReader
    source_onboarding_service: FdeSourceReader

    def validate_scope(self, ctx: RequestContext, mode: str, workspace_ref: str) -> None:
        spec = current_fde_mode(mode)
        if not any(workspace_ref.startswith(prefix) for prefix in spec.scope_prefixes):
            raise ValidationFailed(
                "AI FDE workspaceRef is not valid for the selected mode",
                details={"mode": mode, "workspaceRef": workspace_ref, "scopePrefixes": list(spec.scope_prefixes)},
            )
        if workspace_ref.startswith(("tenant:", "docs:")):
            _require_tenant_scope(ctx, workspace_ref)
            return
        self._read_ref(ctx, workspace_ref)

    def resolve(
        self,
        ctx: RequestContext,
        refs: tuple[str, ...],
        workspace_ref: str,
    ) -> tuple[RetrievedContextItem, ...]:
        normalized = tuple(workspace_ref if ref == "workspace" else ref for ref in refs)
        return tuple(self._context_for_ref(ctx, ref, workspace_ref) for ref in normalized)

    def _context_for_ref(self, ctx: RequestContext, ref: str, workspace_ref: str) -> RetrievedContextItem:
        normalized = workspace_ref if ref == "branch" else ref
        if normalized.startswith(("ontology-branch:", "pipeline-branch:")) and normalized != workspace_ref:
            raise ValidationFailed("AI FDE can attach only its selected ontology branch or Pipeline branch")
        payload = self._read_ref(ctx, normalized)
        return _context_item(ctx, normalized, _source_version(payload), payload)

    def _read_ref(self, ctx: RequestContext, ref: str) -> dict[str, object]:
        if ref.startswith("ontology-branch:"):
            return _ontology_branch_payload(ctx, ref, self.ontology_branch_service)
        if ref.startswith("dataset:"):
            return _json_object(self.dataset_registry_service.get_dataset(ref.removeprefix("dataset:"), ctx=ctx))
        if ref.startswith("pipeline-branch:"):
            return self.pipeline_definition_service.get_branch(ref.removeprefix("pipeline-branch:"), ctx=ctx)
        if ref.startswith("source:"):
            return self.source_onboarding_service.get_source(ref.removeprefix("source:"), ctx=ctx)
        if ref.startswith("function:"):
            return _catalog_item(self.ontology_catalog_service.active_catalog(ctx=ctx), "functionTypes", ref)
        if ref.startswith("osdk-app:"):
            return _json_object(self.osdk_application_service.get_application(ref.removeprefix("osdk-app:"), ctx=ctx))
        if ref.startswith("project:"):
            return self.resource_catalog_service.get_project(ref.removeprefix("project:"), ctx=ctx)
        if ref.startswith("resource:"):
            return self.resource_catalog_service.get_resource(ref.removeprefix("resource:"), ctx=ctx)
        if ref.startswith("model:"):
            return _model_item(self.pipeline_catalog_service.trained_models(ctx=ctx), ref)
        raise ValidationFailed("unsupported AI FDE context reference", details={"contextRef": ref})


def resolve_fde_context(
    ctx: RequestContext,
    refs: tuple[str, ...],
    branch_id: str,
    branch_reader: FdeBranchReader,
    dataset_reader: FdeDatasetReader,
) -> tuple[RetrievedContextItem, ...]:
    items: list[RetrievedContextItem] = []
    for ref in refs:
        if ref == "branch" or ref.startswith("ontology-branch:"):
            items.append(_branch_context(ctx, ref, branch_id, branch_reader))
        elif ref.startswith("dataset:"):
            items.append(_dataset_context(ctx, ref, dataset_reader))
        else:
            raise ValidationFailed("unsupported AI FDE context reference", details={"contextRef": ref})
    return tuple(items)


def _branch_context(
    ctx: RequestContext,
    ref: str,
    branch_id: str,
    branch_reader: FdeBranchReader,
) -> RetrievedContextItem:
    referenced_id = branch_id if ref == "branch" else ref.removeprefix("ontology-branch:")
    if referenced_id != branch_id:
        raise ValidationFailed("AI FDE can attach only its selected ontology branch")
    branch = branch_reader.get_branch(branch_id, ctx=ctx)
    resources = parse_resource_map(_required_text(branch, "yamlText"))
    body = {
        "branchId": branch_id,
        "fingerprint": _required_text(branch, "contentFingerprint"),
        "resources": [
            {"kind": kind, "apiName": api_name, "definition": definition}
            for (kind, api_name), definition in sorted(resources.items())
        ],
    }
    return _context_item(ctx, f"ontology-branch:{branch_id}", str(body["fingerprint"]), body)


def _dataset_context(ctx: RequestContext, ref: str, dataset_reader: FdeDatasetReader) -> RetrievedContextItem:
    dataset_ref = ref.removeprefix("dataset:").strip()
    if not dataset_ref:
        raise ValidationFailed("AI FDE dataset context reference is empty")
    dataset = dataset_reader.get_dataset(dataset_ref, ctx=ctx)
    body = {
        "datasetRef": dataset_ref,
        "datasetId": dataset.get("id"),
        "classification": dataset.get("classification"),
        "primaryKey": dataset.get("primary_key"),
        "storageKind": dataset.get("storage_kind"),
    }
    source_version = str(dataset.get("updated_at") or dataset.get("created_at") or dataset.get("id"))
    return _context_item(ctx, ref, source_version, body)


def _context_item(ctx: RequestContext, source_ref: str, source_version: str, body: JsonObject) -> RetrievedContextItem:
    text = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(text) > _MAX_ATTACHMENT_CHARACTERS:
        text = text[:_MAX_ATTACHMENT_CHARACTERS]
    digest = hashlib.sha256(text.encode()).hexdigest()
    context_id = f"fde-context-{hashlib.sha256(source_ref.encode()).hexdigest()[:20]}"
    return RetrievedContextItem(
        context_id=context_id,
        kind="object",
        text=text,
        source_ref=source_ref,
        source_version=source_version,
        content_hash=f"sha256:{digest}",
        relevance_score=1.0,
        retrieval_method="explicit_attachment",
        security_partition=f"{ctx.tenant_id}:ai-fde",
        token_estimate=max(1, (len(text) + 3) // 4),
    )


def _required_text(value: JsonObject, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValidationFailed(f"AI FDE branch {key} is required")
    return item


def _ontology_branch_payload(
    ctx: RequestContext,
    ref: str,
    reader: FdeBranchReader,
) -> dict[str, object]:
    branch_id = ref.removeprefix("ontology-branch:")
    if not branch_id:
        raise ValidationFailed("AI FDE ontology branch reference is empty")
    branch = reader.get_branch(branch_id, ctx=ctx)
    resources = parse_resource_map(_required_text(branch, "yamlText"))
    return {
        "branchId": branch_id,
        "fingerprint": _required_text(branch, "contentFingerprint"),
        "resources": [
            {"kind": kind, "apiName": api_name, "definition": definition}
            for (kind, api_name), definition in sorted(resources.items())
        ],
    }


def _catalog_item(catalog: Mapping[str, object], field: str, ref: str) -> dict[str, object]:
    api_name = ref.split(":", maxsplit=1)[1]
    items = catalog.get(field)
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping) and item.get("apiName") == api_name:
                return {str(key): value for key, value in item.items()}
    raise ValidationFailed("AI FDE attached function was not found", details={"contextRef": ref})


def _model_item(catalog: Mapping[str, object], ref: str) -> dict[str, object]:
    model_id = ref.removeprefix("model:")
    items = catalog.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping) and model_id in {
                str(item.get("modelId") or ""),
                str(item.get("id") or ""),
                str(item.get("name") or ""),
            }:
                return {str(key): value for key, value in item.items()}
    raise ValidationFailed("AI FDE attached model was not found", details={"contextRef": ref})


def _require_tenant_scope(ctx: RequestContext, workspace_ref: str) -> None:
    value = workspace_ref.split(":", maxsplit=1)[1]
    if value not in {ctx.tenant_id, "platform"}:
        raise ValidationFailed("AI FDE tenant scope is outside the invoking user's tenant")


def _source_version(payload: Mapping[str, object]) -> str:
    for key in (
        "contentFingerprint",
        "fingerprint",
        "updatedAt",
        "updated_at",
        "version",
        "id",
        "sourceName",
    ):
        value = payload.get(key)
        if value is not None:
            return str(value)
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def _json_object(value: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str))
