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


class ActionWritebackReconciliationResult(TypedDict):
    actionRunId: str
    writebackId: str
    status: str
    remoteStatus: str
    remoteResourceId: str
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    alreadyReconciled: NotRequired[bool]


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
    simulate_writeback_outcome_unknown: bool
    simulate_writeback_compensation_required: bool
    external_writeback_uri: str | None = None


@dataclass(frozen=True)
class ActionApplyOutcome:
    response: ActionApplyResponse | None = None
    deferred_error: Exception | None = None
