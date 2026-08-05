"""Generated from the active Ontology; do not edit by hand."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from foundry_lite.application.osdk_actions import action_type

from foundry_lite_sdk.runtime import (
    GeneratedActionType,
)

ONTOLOGY_CONTRACT_FINGERPRINT = "16d0f0b5844eab24"


class ApproveOrderParams(TypedDict):
    reason: str


ApproveOrder: GeneratedActionType[ApproveOrderParams] = GeneratedActionType(
    action_type(
        "ApproveOrder",
        target_object_type="Order",
        target_kind="object",
        parameter_names=("reason",),
        required_parameters=("reason",),
    )
)

ACTIONS: Mapping[str, object] = {
    "ApproveOrder": ApproveOrder,
}

__all__ = [
    "ACTIONS",
    "ONTOLOGY_CONTRACT_FINGERPRINT",
    "ApproveOrder",
    "ApproveOrderParams",
]
