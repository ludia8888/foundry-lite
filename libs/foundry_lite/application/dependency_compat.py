"""Compatibility helpers for dependency bundle construction."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Protocol


class BundleFactory(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


CORE_DEPENDENCY_BUNDLE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "paths": ("root", "storage_root"),
    "security": (
        "engine",
        "policy",
        "metadata_repository",
        "destructive_development_admin",
        "osdk_application_repository",
        "oauth_session_repository",
        "oauth_token_issuer",
        "secret_provider",
        "secret_vault",
    ),
    "action": ("action_repository",),
    "data": (
        "ontology_repository",
        "ontology_branch_repository",
        "pipeline_repository",
        "transform_repository",
        "materialization_repository",
        "dataset_quality_repository",
        "compute_adapter",
        "dataset_repository",
        "dataset_transaction_repository",
        "dataset_version_repository",
        "dataset_storage",
    ),
    "object_store": (
        "object_index_repository",
        "object_index_row_hash_repository",
        "object_read_repository",
        "object_set_repository",
        "search_adapter",
    ),
    "runtime": (
        "runtime_repository",
        "erasure_repository",
        "insight_review_repository",
        "stream_adapter",
        "workflow_adapter",
        "backup_artifact_store",
    ),
    "aip": (
        "ai_eval_repository",
        "ai_run_repository",
        "embedding_model_adapter",
        "completion_model_adapter",
        "vision_embedding_model_adapter",
        "language_model_adapter",
        "model_registry_repository",
        "context_provider",
        "prompt_artifact_store",
        "citation_source_verifier",
        "tool_executor",
    ),
    "media": (
        "media_repository",
        "media_derivative_repository",
        "media_reference_binding_repository",
        "media_access_cache_repository",
        "media_storage",
        "media_processor",
        "media_preview_renderer",
        "external_media_reader",
        "content_index_adapter",
    ),
    "source": (
        "connector_adapter",
        "connector_registry_repository",
        "source_registry_repository",
        "source_management_repository",
        "source_database_adapter",
    ),
}


def fill_missing_bundles_from_flat_overrides(
    bundles: MutableMapping[str, object | None],
    flat_overrides: MutableMapping[str, object],
    bundle_factories: Mapping[str, BundleFactory],
) -> None:
    for bundle_name, bundle in list(bundles.items()):
        if bundle is not None:
            continue
        field_names = CORE_DEPENDENCY_BUNDLE_FIELDS[bundle_name]
        present = {name: flat_overrides.pop(name) for name in field_names if name in flat_overrides}
        if len(present) != len(field_names):
            missing = sorted(set(field_names) - set(present))
            raise TypeError(f"CoreDependencies requires bundle '{bundle_name}' or flat dependency fields: {missing}")
        bundles[bundle_name] = bundle_factories[bundle_name](**present)


def apply_flat_dependency_overrides(
    bundles: MutableMapping[str, object | None],
    flat_overrides: MutableMapping[str, object],
    bundle_factories: Mapping[str, BundleFactory],
) -> None:
    for bundle_name, field_names in CORE_DEPENDENCY_BUNDLE_FIELDS.items():
        changes = {name: flat_overrides.pop(name) for name in field_names if name in flat_overrides}
        if not changes:
            continue
        bundle = bundles[bundle_name]
        if bundle is None:
            raise TypeError(f"CoreDependencies requires bundle '{bundle_name}' before flat overrides can apply")
        values = {name: getattr(bundle, name) for name in field_names}
        values.update(changes)
        bundles[bundle_name] = bundle_factories[bundle_name](**values)
    if flat_overrides:
        names = sorted(flat_overrides)
        raise TypeError(f"unexpected CoreDependencies keyword argument(s): {names}")
