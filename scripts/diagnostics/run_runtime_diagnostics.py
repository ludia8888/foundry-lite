from __future__ import annotations

import argparse
import cProfile
import faulthandler
import gc
import json
import os
import platform
import pstats
import shutil
import sys
import time
import tracemalloc
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "diagnostics"


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the core demo with Python runtime diagnostics enabled.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--storage-root", default=".foundry-lite-diagnostics")
    parser.add_argument(
        "--console-traces",
        action="store_true",
        help="Also print OpenTelemetry spans to stdout. Artifacts are always written to disk.",
    )
    args = parser.parse_args(argv)

    args.output.mkdir(parents=True, exist_ok=True)
    fault_log_path = args.output / "faulthandler.log"
    profile_path = args.output / "demo_profile.pstats"
    profile_text_path = args.output / "demo_profile_top.txt"
    summary_path = args.output / "runtime_diagnostics.json"
    storage_root = (ROOT / args.storage_root).resolve()
    if ROOT not in storage_root.parents:
        raise SystemExit(f"diagnostics storage root must stay inside repo: {storage_root}")
    if storage_root.exists():
        shutil.rmtree(storage_root)

    if args.console_traces:
        os.environ["FOUNDRY_LITE_OTEL_CONSOLE"] = "1"
    sys.path[:0] = [str(ROOT / "libs"), str(ROOT / "apps" / "cli"), str(ROOT / "apps" / "api")]

    from foundry_lite.application.foundry import FoundryLite
    from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
    from foundry_lite.observability.tracing import configure_observability

    configure_observability("foundry-lite-diagnostics")
    warnings.simplefilter("default")
    captured_warnings: list[warnings.WarningMessage] = []

    with fault_log_path.open("w", encoding="utf-8") as fault_file:
        faulthandler.enable(file=fault_file)
        tracemalloc.start(25)
        profiler = cProfile.Profile()
        started_at = time.perf_counter()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("default")
            foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
            try:
                result = profiler.runcall(foundry.demo.run)
            finally:
                foundry.close()
            captured_warnings.extend(caught)
        duration_seconds = time.perf_counter() - started_at

    profiler.dump_stats(profile_path)
    with profile_text_path.open("w", encoding="utf-8") as profile_text:
        stats = pstats.Stats(profiler, stream=profile_text)
        stats.sort_stats("cumulative")
        stats.print_stats(40)

    snapshot = tracemalloc.take_snapshot()
    memory_top = [
        {
            "file": str(stat.traceback[0].filename),
            "line": stat.traceback[0].lineno,
            "size_kib": round(stat.size / 1024, 2),
            "count": stat.count,
        }
        for stat in snapshot.statistics("lineno")[:25]
    ]
    tracemalloc.stop()

    summary = {
        "status": "succeeded",
        "duration_seconds": round(duration_seconds, 4),
        "python": sys.version,
        "platform": platform.platform(),
        "gc_count": gc.get_count(),
        "gc_stats": gc.get_stats(),
        "warnings": [
            {
                "category": warning.category.__name__,
                "message": str(warning.message),
                "file": warning.filename,
                "line": warning.lineno,
            }
            for warning in captured_warnings
        ],
        "artifacts": {
            "faulthandler_log": str(fault_log_path.relative_to(ROOT)),
            "profile_stats": str(profile_path.relative_to(ROOT)),
            "profile_top": str(profile_text_path.relative_to(ROOT)),
        },
        "memory_top": memory_top,
        "demo": _json_ready(result),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Runtime diagnostics written to {summary_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
