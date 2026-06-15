from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, TypedDict


class ActionApplyResponse(TypedDict):
    actionRunId: str
    status: str
    target: Mapping[str, object]
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    patch: NotRequired[Mapping[str, object]]
    idempotentReplay: NotRequired[bool]


@dataclass(frozen=True)
class ActionApplyCommand:
    action_api_name: str
    object_type: str
    object_id: str
    expected_object_version: int
    params: dict[str, object]
    idempotency_key: str
    request_fingerprint: str
    simulate_writeback_failure: bool


@dataclass(frozen=True)
class ActionApplyOutcome:
    response: ActionApplyResponse | None = None
    deferred_error: Exception | None = None
