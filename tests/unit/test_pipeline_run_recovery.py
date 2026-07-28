from datetime import UTC, datetime, timedelta

from foundry_lite.application.services.pipeline_run_recovery import (
    is_stale_pipeline_execution,
    replayed_pipeline_run_action,
)


def test_only_hour_old_executing_pipeline_run_is_stale() -> None:
    now = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)
    recent = {"status": "executing", "started_at": (now - timedelta(minutes=59)).isoformat()}
    stale = {"status": "executing", "started_at": (now - timedelta(hours=1)).isoformat()}
    recently_claimed = {
        "status": "executing",
        "started_at": (now - timedelta(hours=2)).isoformat(),
        "timeline": [
            {
                "event": "pipeline.run.execution_claimed",
                "at": (now - timedelta(minutes=1)).isoformat(),
            }
        ],
    }

    assert is_stale_pipeline_execution(recent, now=now) is False
    assert is_stale_pipeline_execution(stale, now=now) is True
    assert is_stale_pipeline_execution(recently_claimed, now=now) is False


def test_queued_replay_executes_while_terminal_replay_only_reads() -> None:
    assert replayed_pipeline_run_action({"status": "running"}) == "execute"
    assert replayed_pipeline_run_action({"status": "succeeded"}) == "read"
