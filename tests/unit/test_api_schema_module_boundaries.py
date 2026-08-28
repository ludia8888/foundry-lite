"""Regression coverage for domain-owned API schema modules."""

from __future__ import annotations

from types import ModuleType

from foundry_lite_api import (
    schemas,
    schemas_actions,
    schemas_aip,
    schemas_media,
    schemas_ontology,
    schemas_osdk,
    schemas_pipelines,
    schemas_sources,
)
from pydantic import BaseModel


def _owned_request_models(module: ModuleType) -> dict[str, type[BaseModel]]:
    return {
        name: value
        for name, value in vars(module).items()
        if name.endswith("Request")
        and isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == module.__name__
    }


def test_domain_schema_models_remain_available_from_compatibility_module() -> None:
    modules = (
        schemas_actions,
        schemas_aip,
        schemas_media,
        schemas_ontology,
        schemas_osdk,
        schemas_pipelines,
        schemas_sources,
    )

    owned_models = {name: model for module in modules for name, model in _owned_request_models(module).items()}

    assert owned_models
    assert all(getattr(schemas, name) is model for name, model in owned_models.items())
