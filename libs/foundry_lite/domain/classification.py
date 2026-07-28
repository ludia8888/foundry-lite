"""Canonical information-classification lattice shared across product planes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# Each tuple is one sensitivity level, from least to most sensitive.
# ``SECRET`` is the media-facing name and ``RESTRICTED`` is the Pipeline/Dataset
# name for the same highest level. Grouping aliases by level prevents product
# planes from drifting while avoiding credential-like key/value declarations.
_CLASSIFICATION_LEVEL_GROUPS = (
    ("UNCLASSIFIED", "PUBLIC"),
    ("INTERNAL",),
    ("CONFIDENTIAL",),
    ("SECRET", "RESTRICTED"),
)

CLASSIFICATION_RANKS: Mapping[str, int] = MappingProxyType(
    {
        classification: rank
        for rank, classifications in enumerate(_CLASSIFICATION_LEVEL_GROUPS)
        for classification in classifications
    }
)


def normalize_classification(value: object) -> str:
    """Return a stable uppercase label, defaulting empty values to unclassified."""

    return value.strip().upper() if isinstance(value, str) and value.strip() else "UNCLASSIFIED"


def classification_rank(value: object) -> int | None:
    """Return the known lattice rank while leaving unknown labels fail-closed."""

    return CLASSIFICATION_RANKS.get(normalize_classification(value))


__all__ = ["CLASSIFICATION_RANKS", "classification_rank", "normalize_classification"]
