"""Recover ambiguous Pipeline DAG dispatches with deterministic workflow IDs."""

from __future__ import annotations

import logging
import os
import signal
from threading import Event

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

from foundry_lite_worker.failure_logging import log_tick_failure

_LOGGER = logging.getLogger(__name__)


def run_control_loop(stop_event: Event | None = None) -> None:
    requested_stop = stop_event or Event()
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_HOME", ".foundry-lite"),
            adapter_profile=os.getenv("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        ),
        should_initialize_schema=False,
    )
    try:
        interval = max(1.0, float(os.getenv("FOUNDRY_LITE_PIPELINE_CONTROL_INTERVAL_SECONDS", "5")))
        worker_id = os.getenv("FOUNDRY_LITE_WORKER_ID", "pipeline-control")
        tick_number = 0
        while not requested_stop.is_set():
            # The loop is cross-tenant, so a tick carries no single tenant: correlate a
            # failure to its tick with a per-tick request_id instead.
            tick_number += 1
            request_id = f"{worker_id}:tick:{tick_number}"
            # A single failing tick must not kill the durable recovery loop; log it and
            # continue to the next interval so transient DB/adapter errors self-heal.
            try:
                foundry._services.pipelines.control.tick(limit=100)
                foundry._services.pipelines.preview.recover_preview_dispatches(limit=100)
            except Exception as exc:  # noqa: BLE001 - keep the durable control loop alive
                log_tick_failure(
                    _LOGGER,
                    "pipeline.control.tick_failed",
                    request_id=request_id,
                    exc=exc,
                )
            # wait() returns immediately when a shutdown signal sets the event, so the
            # loop exits promptly instead of finishing the full sleep interval.
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
    run_control_loop(stop_event)


if __name__ == "__main__":
    main()
