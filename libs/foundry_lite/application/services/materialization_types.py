from __future__ import annotations

from typing import Final, NoReturn

from foundry_lite.domain.errors import ValidationFailed

SUPPORTED_MATERIALIZATION_TYPES: Final = frozenset({"action_log", "object_snapshot"})


def supported_materialization_type(materialization_type: str) -> str:
    if materialization_type in SUPPORTED_MATERIALIZATION_TYPES:
        return materialization_type
    unsupported_materialization_type(materialization_type)


def unsupported_materialization_type(materialization_type: str) -> NoReturn:
    raise ValidationFailed(
        "unsupported materialization type",
        details={
            "materialization_type": materialization_type,
            "supported_types": sorted(SUPPORTED_MATERIALIZATION_TYPES),
        },
    )
