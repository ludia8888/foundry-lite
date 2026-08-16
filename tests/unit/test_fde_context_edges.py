from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from foundry_lite.application.services.aip.fde_context import FdeContextService, resolve_fde_context
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_CTX = RequestContext(tenant_id="tenant-a", actor_user_id="builder-1", roles=("data_engineer",))
_YAML = """
objectTypes:
  - apiName: Order
    properties:
      - apiName: orderId
        type: string
functionTypes:
  - apiName: scoreOrder
    version: 1.0.0
"""


class _Reader:
    def get_branch(self, branch_id: str, **_kwargs: object) -> dict[str, object]:
        if branch_id.startswith("pipeline"):
            return {"id": branch_id, "version": 3, "nodes": []}
        return {
            "id": branch_id,
            "yamlText": _YAML,
            "contentFingerprint": "sha256:branch",
        }

    def get_dataset(self, dataset_ref: str, **_kwargs: object) -> dict[str, object]:
        return {
            "id": "dataset-1",
            "ref": dataset_ref,
            "classification": "internal",
            "primary_key": ["order_id"],
            "storage_kind": "parquet",
            "updated_at": "2026-08-13T00:00:00Z",
        }

    def active_catalog(self, **_kwargs: object) -> dict[str, object]:
        return {"functionTypes": [{"apiName": "scoreOrder", "version": "1.0.0"}]}

    def get_application(self, application_id: str, **_kwargs: object) -> dict[str, object]:
        return {"id": application_id, "updatedAt": "2026-08-13T00:00:00Z"}

    def trained_models(self, **_kwargs: object) -> dict[str, object]:
        return {"items": [{"modelId": "demand-model", "version": "v2"}]}

    def get_project(self, project_id: str, **_kwargs: object) -> dict[str, object]:
        return {"id": project_id, "version": 2}

    def get_resource(self, rid: str, **_kwargs: object) -> dict[str, object]:
        return {"id": rid, "version": 4}

    def get_source(self, source_name: str, **_kwargs: object) -> dict[str, object]:
        return {"sourceName": source_name, "status": "active"}


def _service() -> FdeContextService:
    reader = _Reader()
    service = FdeContextService()
    service.bind_collaborators(
        {
            "dataset_registry_service": reader,
            "ontology_branch_service": reader,
            "ontology_catalog_service": reader,
            "osdk_application_service": reader,
            "pipeline_catalog_service": reader,
            "pipeline_definition_service": reader,
            "resource_catalog_service": reader,
            "source_onboarding_service": reader,
        }
    )
    return service


@pytest.mark.parametrize(
    ("mode", "workspace"),
    [
        ("ontology_editing", "ontology-branch:branch-1"),
        ("data_integration", "pipeline-branch:pipeline-1"),
        ("data_connection", "source:orders"),
        ("functions_editing", "function:scoreOrder"),
        ("osdk_react", "osdk-app:restaurant"),
        ("governance", "project:operations"),
        ("governance", "resource:ri.resource.1"),
        ("ml", "model:demand-model"),
        ("exploration", "dataset:raw.orders"),
        ("exploration", "tenant:tenant-a"),
        ("platform_qa", "docs:tenant-a"),
    ],
)
def test_fde_context_scope_reads_every_supported_resource_without_bypassing_tenant(mode: str, workspace: str) -> None:
    _service().validate_scope(_CTX, mode, workspace)


def test_fde_context_resolves_explicit_resources_with_stable_server_owned_evidence() -> None:
    refs = (
        "ontology-branch:branch-1",
        "dataset:raw.orders",
        "source:orders",
        "function:scoreOrder",
        "osdk-app:restaurant",
        "project:operations",
        "resource:ri.resource.1",
        "model:demand-model",
    )
    items = _service().resolve(_CTX, refs, "ontology-branch:branch-1")
    pipeline = _service().resolve(
        _CTX,
        ("pipeline-branch:pipeline-1",),
        "pipeline-branch:pipeline-1",
    )[0]

    assert [item.source_ref for item in items] == list(refs)
    assert all(item.security_partition == "tenant-a:ai-fde" for item in items)
    assert all(item.content_hash.startswith("sha256:") for item in items)
    assert json.loads(items[3].text)["apiName"] == "scoreOrder"
    assert items[7].source_version == "v2"
    assert pipeline.source_version == "3"


def test_fde_context_rejects_cross_scope_branch_tenant_unknown_and_missing_resources() -> None:
    service = _service()
    invalid = (
        lambda: service.validate_scope(_CTX, "ontology_editing", "dataset:raw.orders"),
        lambda: service.validate_scope(_CTX, "exploration", "tenant:tenant-b"),
        lambda: service.resolve(_CTX, ("ontology-branch:branch-2",), "ontology-branch:branch-1"),
        lambda: service.resolve(_CTX, ("function:missing",), "ontology-branch:branch-1"),
        lambda: service.resolve(_CTX, ("model:missing",), "ontology-branch:branch-1"),
        lambda: service.resolve(_CTX, ("unsupported:value",), "ontology-branch:branch-1"),
    )

    for invoke in invalid:
        with pytest.raises(ValidationFailed):
            invoke()


def test_legacy_fde_context_resolver_validates_branch_and_dataset_references() -> None:
    reader = _Reader()
    items = resolve_fde_context(
        _CTX,
        ("branch", "ontology-branch:branch-1", "dataset:raw.orders"),
        "branch-1",
        reader,
        reader,
    )

    assert len(items) == 3
    assert json.loads(items[0].text)["branchId"] == "branch-1"
    with pytest.raises(ValidationFailed, match="selected ontology branch"):
        resolve_fde_context(_CTX, ("ontology-branch:branch-2",), "branch-1", reader, reader)
    with pytest.raises(ValidationFailed, match="dataset context reference is empty"):
        resolve_fde_context(_CTX, ("dataset: ",), "branch-1", reader, reader)
    with pytest.raises(ValidationFailed, match="unsupported"):
        resolve_fde_context(_CTX, ("model:missing",), "branch-1", reader, reader)


def test_fde_context_requires_branch_metadata_and_bounds_attachment_text() -> None:
    bad_reader = SimpleNamespace(
        get_branch=lambda *_args, **_kwargs: {"yamlText": ""},
    )
    with pytest.raises(ValidationFailed, match="yamlText"):
        resolve_fde_context(_CTX, ("branch",), "branch-1", bad_reader, _Reader())

    large = _Reader()
    large.get_resource = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "id": "large-resource",
        "payload": "x" * 10_000,
    }
    service = _service()
    service.resource_catalog_service = large
    item = service.resolve(_CTX, ("resource:large-resource",), "resource:large-resource")[0]
    assert len(item.text) == 4000
    assert item.token_estimate == 1000
