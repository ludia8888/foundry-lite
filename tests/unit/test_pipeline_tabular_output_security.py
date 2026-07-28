from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports import DatasetRow
from foundry_lite.application.services.pipeline_tabular_output_security import (
    ensure_tabular_output_dataset,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class _DatasetRegistry:
    def __init__(self, classifications: dict[str, str]) -> None:
        self.classifications = dict(classifications)

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        classification: str | None = None,
    ) -> DatasetRow:
        del ctx
        self.classifications[dataset_ref] = classification or "UNCLASSIFIED"
        return _dataset(dataset_ref, self.classifications[dataset_ref])

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        del ctx
        return _dataset(dataset_ref, self.classifications[dataset_ref])

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow | None:
        del ctx
        classification = self.classifications.get(dataset_ref)
        return None if classification is None else _dataset(dataset_ref, classification)


def test_tabular_output_inherits_strongest_input_classification() -> None:
    registry = _DatasetRegistry({"raw.public": "PUBLIC", "raw.secret": "SECRET"})

    ensure_tabular_output_dataset(
        registry,
        RequestContext(),
        "work.output",
        {"public": "raw.public", "secret": "raw.secret"},
    )

    assert registry.classifications["work.output"] == "SECRET"


def test_existing_tabular_output_cannot_weaken_stronger_input() -> None:
    registry = _DatasetRegistry({"raw.secret": "SECRET", "work.output": "CONFIDENTIAL"})

    with pytest.raises(ValidationFailed, match="would weaken"):
        ensure_tabular_output_dataset(
            registry,
            RequestContext(),
            "work.output",
            {"secret": "raw.secret"},
        )


def _dataset(dataset_ref: str, classification: str) -> DatasetRow:
    return cast(
        DatasetRow,
        {
            "id": dataset_ref,
            "classification": classification,
        },
    )
