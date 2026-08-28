"""Contract tests for ontology definition reader adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.ontology_definition_reader import OntologyDefinitionRead
from foundry_lite.infrastructure.adapters.local_ontology_definition_reader import LocalOntologyDefinitionReader


def test_local_ontology_definition_reader_returns_utf8_content_and_integrity_metadata(tmp_path: Path) -> None:
    yaml_text = "objectTypes:\n  - apiName: Order\n"
    path = tmp_path / "ontology.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    content = LocalOntologyDefinitionReader().read_definition(OntologyDefinitionRead(str(path)))

    data = yaml_text.encode("utf-8")
    assert content.yaml_text == yaml_text
    assert content.byte_size == len(data)
    assert content.content_hash == f"sha256:{hashlib.sha256(data).hexdigest()}"


def test_local_ontology_definition_reader_reports_missing_location_without_leaking_path(tmp_path: Path) -> None:
    with pytest.raises(AdapterError) as captured:
        LocalOntologyDefinitionReader().read_definition(OntologyDefinitionRead(str(tmp_path / "customer-secret.yaml")))

    assert captured.value.failure.kind == "not_found"
    assert captured.value.failure.is_retryable is False
    assert "customer-secret" not in str(captured.value.details)


def test_local_ontology_definition_reader_rejects_non_utf8_content(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(AdapterError) as captured:
        LocalOntologyDefinitionReader().read_definition(OntologyDefinitionRead(str(path)))

    assert captured.value.failure.kind == "validation"
    assert captured.value.failure.is_retryable is False


def test_local_ontology_definition_reader_declares_failure_contract() -> None:
    contract = LocalOntologyDefinitionReader().failure_contract()

    assert contract.adapter_profile == "local-ontology-definition-reader"
    assert [(mode.operation, mode.kind, mode.is_retryable) for mode in contract.modes] == [
        ("read_definition", "not_found", False),
        ("read_definition", "validation", False),
        ("read_definition", "unavailable", True),
    ]
