"""Keep bounded Foundry-lite workers alive without shell loops or hidden exits."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed interpreter and module allowlist; remove if commands become configurable.
import sys
import time
from typing import Final

_ALLOWED_MODULES: Final = frozenset(
    {
        "foundry_lite_worker.outbox_publisher",
        "foundry_lite_worker.source_scheduler",
        "foundry_lite_worker.pipeline_control",
        "foundry_lite_worker.action_control",
    }
)


def supervise(module: str, *, interval_seconds: float, max_cycles: int = 0) -> int:
    if module not in _ALLOWED_MODULES:
        raise ValueError("worker_module_not_allowed")
    if interval_seconds <= 0 or interval_seconds > 300 or max_cycles < 0:
        raise ValueError("worker_supervisor_configuration_invalid")
    cycle = 0
    while max_cycles == 0 or cycle < max_cycles:
        cycle += 1
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell; remove if arguments become unbounded.
            (sys.executable, "-m", module),
            check=False,
            shell=False,
        )
        event = "worker_cycle_complete" if completed.returncode == 0 else "worker_cycle_failed"
        print(json.dumps({"event": event, "module": module, "cycle": cycle, "exitCode": completed.returncode}))
        if max_cycles == 0 or cycle < max_cycles:
            time.sleep(interval_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervise a bounded Foundry-lite worker module.")
    parser.add_argument("--module", required=True, choices=sorted(_ALLOWED_MODULES))
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-cycles", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        return supervise(args.module, interval_seconds=args.interval_seconds, max_cycles=args.max_cycles)
    except ValueError:
        print(json.dumps({"event": "configuration_error", "reason": "invalid_worker_supervisor_configuration"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
