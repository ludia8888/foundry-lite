"""Pure transform graph bounds."""

from __future__ import annotations

from foundry_lite.domain.errors import ValidationFailed


def validate_transform_graph_bounds(max_depth: int, max_runs: int) -> tuple[int, int]:
    if max_depth < 1 or max_depth > 7 or max_runs < 1 or max_runs > 50:
        raise ValidationFailed("transform graph bounds must be depth 1-7 and runs 1-50")
    return max_depth, max_runs
