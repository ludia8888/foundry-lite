from __future__ import annotations

from typing import TypedDict

from foundry_lite.application.ports import ObjectQueryItem, ObjectSetDefinition


class NormalizedObjectSetDefinition(TypedDict):
    definition: ObjectSetDefinition
    expires_at: str | None


ObjectSetMembers = tuple[list[str], list[ObjectQueryItem]]
