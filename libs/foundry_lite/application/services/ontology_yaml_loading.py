"""Bounded canonical loader for ontology YAML text."""

from __future__ import annotations

import yaml

from foundry_lite.application.services.ontology_yaml import YamlObject, yaml_object, yaml_parse_error_details
from foundry_lite.domain.errors import ValidationFailed


def load_ontology_yaml_text(yaml_text: str) -> YamlObject:
    """Parse ontology YAML into the canonical string-keyed mapping."""
    try:
        definition: object = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValidationFailed("ontology yaml parse failed", details=yaml_parse_error_details(exc)) from exc
    return yaml_object(definition, "ontology yaml")


__all__ = ["load_ontology_yaml_text"]
