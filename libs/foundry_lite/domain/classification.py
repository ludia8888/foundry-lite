"""Canonical information-classification lattice shared across product planes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# ``SECRET`` is the media-facing name and ``RESTRICTED`` is the Pipeline/Dataset
# name for the same highest sensitivity level. Keeping both aliases here prevents
# one product plane from treating a stronger value as an unknown weaker value.
CLASSIFICATION_RANKS: Mapping[str, int] = MappingProxyType(
    {
        "UNCLASSIFIED": 0,
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "SECRET": 3,
        "RESTRICTED": 3,
    }
)


def normalize_classification(value: object) -> str:
    """Return a stable uppercase label, defaulting empty values to unclassified."""

    return value.strip().upper() if isinstance(value, str) and value.strip() else "UNCLASSIFIED"


def classification_rank(value: object) -> int | None:
    """Return the known lattice rank while leaving unknown labels fail-closed."""

    return CLASSIFICATION_RANKS.get(normalize_classification(value))


__all__ = ["CLASSIFICATION_RANKS", "classification_rank", "normalize_classification"]
