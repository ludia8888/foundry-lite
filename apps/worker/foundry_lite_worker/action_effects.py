"""Deliver durable after-commit Action effects from receipt/outbox evidence."""

from __future__ import annotations

import logging
import os
import signal
from threading import Event

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

from foundry_lite_worker.failure_logging import log_tick_failure

_LOGGER = logging.getLogger(__name__)


def run_effect_loop(stop_event: Event | None = None) -> None:
    requested_stop = stop_event or Event()
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_STORAGE_ROOT"),
        ),
        should_initialize_schema=False,
    )
    try:
        interval = max(1.0, float(os.getenv("FOUNDRY_LITE_ACTION_EFFECT_INTERVAL_SECONDS", "5")))
        lease_seconds = max(1, min(int(os.getenv("FOUNDRY_LITE_ACTION_EFFECT_LEASE_SECONDS", "30")), 3600))
        concurrency = max(1, min(int(os.getenv("FOUNDRY_LITE_ACTION_EFFECT_CONCURRENCY", "4")), 32))
        worker_id = os.getenv("FOUNDRY_LITE_WORKER_ID", "action-effects")
        tick_number = 0
        while not requested_stop.is_set():
            tick_number += 1
            try:
                foundry._services.action_effects.deliver_all(
                    worker_id=worker_id,
                    limit=100,
                    lease_seconds=lease_seconds,
                    concurrency=concurrency,
                )
            except Exception as exc:  # noqa: BLE001 - one provider outage must not stop the durable worker.
                log_tick_failure(
                    _LOGGER,
                    "action.effects.tick_failed",
                    request_id=f"{worker_id}:tick:{tick_number}",
                    exc=exc,
                )
            requested_stop.wait(interval)
    finally:
        foundry.close()


def _install_signal_handlers(stop_event: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def main() -> None:
    stop_event = Event()
    _install_signal_handlers(stop_event)
    run_effect_loop(stop_event)


if __name__ == "__main__":
    main()
