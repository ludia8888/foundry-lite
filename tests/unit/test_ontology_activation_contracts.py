"""Direct contract tests for cross-resource Ontology activation validation."""

from __future__ import annotations

from typing import Any, cast

from foundry_lite.application.services import ontology_activation_contracts as contracts
from pytest import MonkeyPatch


def test_validate_activation_contracts_runs_all_validators_before_returning_functions(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []
    expected = {"ComputeRisk": cast(Any, object())}
    monkeypatch.setattr(
        contracts,
        "validate_ontology_media_sets",
        lambda *_args: calls.append("media"),
    )
    monkeypatch.setattr(
        contracts,
        "validate_ontology_definition",
        lambda *_args: calls.append("definition"),
    )
    monkeypatch.setattr(
        contracts,
        "validate_ontology_interfaces",
        lambda *_args: calls.append("interfaces"),
    )
    monkeypatch.setattr(
        contracts,
        "validate_ontology_functions",
        lambda *_args: calls.append("functions") or expected,
    )

    result = contracts.validate_activation_contracts(
        cast(Any, object()),
        cast(Any, object()),
        cast(Any, object()),
        cast(Any, {}),
        cast(Any, object()),
        cast(Any, object()),
    )

    assert result is expected
    assert calls == ["media", "definition", "interfaces", "functions"]
