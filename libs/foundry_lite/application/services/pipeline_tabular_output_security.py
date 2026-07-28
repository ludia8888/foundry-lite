"""Classification inheritance for tabular Pipeline output datasets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import DatasetRow
from foundry_lite.application.services.pipeline_v2_runtime_security import (
    require_dataset_classification,
    strongest_classification,
)
from foundry_lite.domain.context import RequestContext


class PipelineOutputDatasetRegistry(Protocol):
    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        classification: str | None = None,
    ) -> DatasetRow: ...

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow: ...

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow | None: ...


def ensure_tabular_output_dataset(
    registry: PipelineOutputDatasetRegistry,
    ctx: RequestContext,
    output_ref: str,
    inputs: Mapping[str, str],
) -> None:
    """Create the output at the strongest input classification and reject weaker reuse."""

    if not inputs:
        registry.ensure_dataset(output_ref, ctx=ctx)
        return
    classifications = [_dataset_classification(registry, ctx, ref) for ref in inputs.values()]
    required = strongest_classification(classifications)
    existing = registry.find_dataset(output_ref, ctx=ctx)
    if existing is not None:
        require_dataset_classification(existing["classification"], required, dataset_ref=output_ref)
        return
    output = registry.ensure_dataset(output_ref, ctx=ctx, classification=required)
    require_dataset_classification(output["classification"], required, dataset_ref=output_ref)


def _dataset_classification(
    registry: PipelineOutputDatasetRegistry,
    ctx: RequestContext,
    dataset_ref: str,
) -> str:
    value = registry.get_dataset(dataset_ref, ctx=ctx)["classification"]
    return str(value) if value is not None else "UNCLASSIFIED"
