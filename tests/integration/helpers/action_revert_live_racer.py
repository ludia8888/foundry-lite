"""One process in the PostgreSQL concurrent Action revert proof."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import Event

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies


def main() -> int:
    db_url, run_id, idempotency_key, ready_value, barrier_value, result_value = sys.argv[1:]
    ready = Path(ready_value)
    barrier = Path(barrier_value)
    result_path = Path(result_value)
    foundry = FoundryLite(dependencies=create_runtime_core_dependencies(db_url=db_url))
    ready.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
    deadline = 20.0
    elapsed = 0.0
    waiter = Event()
    while not barrier.exists() and elapsed < deadline:
        waiter.wait(0.01)
        elapsed += 0.01
    if not barrier.exists():
        result_path.write_text(json.dumps({"status": "timeout"}), encoding="utf-8")
        return 2
    try:
        result = foundry.actions.revert(run_id, idempotency_key=idempotency_key, ctx=demo_admin_context())
        payload = {"status": "succeeded", "result": result}
    except FoundryLiteError as exc:
        payload = {"status": "rejected", "code": exc.code, "message": str(exc)}
    result_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
