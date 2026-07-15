"""Application service helpers for source scheduler views workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from foundry_lite.application.services.source_scheduler_config import SourceScheduleDecision


def scheduler_tick_view(
    decisions: Sequence[SourceScheduleDecision],
    started: Sequence[Mapping[str, object]],
    skipped: Sequence[Mapping[str, object]],
    now: datetime,
    *,
    max_runs: int,
) -> dict[str, object]:
    due = [decision.as_dict() for decision in decisions if decision.is_due]
    return {
        "status": "evaluated",
        "evaluatedAt": _iso(now),
        "evaluated": len(decisions),
        "decisions": [decision.as_dict() for decision in decisions],
        "due": due,
        "started": [dict(row) for row in started],
        "skipped": [dict(row) for row in skipped],
        "maxRuns": max_runs,
    }


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
