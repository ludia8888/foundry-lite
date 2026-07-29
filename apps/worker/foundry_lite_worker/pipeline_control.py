"""Recover ambiguous Pipeline DAG dispatches with deterministic workflow IDs."""

from __future__ import annotations

import os
import time

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies


def run_control_loop() -> None:
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_STORAGE_ROOT"),
        )
    )
    interval = max(1.0, float(os.getenv("FOUNDRY_LITE_PIPELINE_CONTROL_INTERVAL_SECONDS", "5")))
    while True:
        foundry._services.pipelines.control.tick(limit=100)
        foundry._services.pipelines.preview.recover_preview_dispatches(limit=100)
        time.sleep(interval)


def main() -> None:
    run_control_loop()


if __name__ == "__main__":
    main()
