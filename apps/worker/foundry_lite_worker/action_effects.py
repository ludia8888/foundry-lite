"""Deliver durable after-commit Action effects from receipt/outbox evidence."""

from __future__ import annotations

import logging
import os
import signal
from threading import Event

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

_LOGGER = logging.getLogger(__name__)


def run_effect_loop(stop_event: Event | None = None) -> None:
    requested_stop = stop_event or Event()
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_STORAGE_ROOT"),
        )
    )
    interval = max(1.0, float(os.getenv("FOUNDRY_LITE_ACTION_EFFECT_INTERVAL_SECONDS", "5")))
    worker_id = os.getenv("FOUNDRY_LITE_WORKER_ID", "action-effects")
    tick_number = 0
    while not requested_stop.is_set():
        tick_number += 1
        try:
            foundry._services.action_effects.deliver_all(worker_id=worker_id, limit=100)
        except Exception:  # noqa: BLE001 - one provider outage must not stop the durable worker.
            _LOGGER.exception(
                "action.effects.tick_failed request_id=%s:tick:%s",
                worker_id,
                tick_number,
            )
        requested_stop.wait(interval)


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
