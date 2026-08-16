from __future__ import annotations

import multiprocessing
import os
import queue
from pathlib import Path
from typing import Protocol

import pytest
from foundry_lite.infrastructure.adapters.fastembed_model_cache import fastembed_model_load_lock


class _PidQueue(Protocol):
    def put(self, value: int) -> None: ...


class _ReleaseEvent(Protocol):
    def wait(self, timeout: float | None = None) -> bool: ...


def _hold_model_lock(cache_dir: str, entered: _PidQueue, release: _ReleaseEvent) -> None:
    os.environ["FASTEMBED_CACHE_PATH"] = cache_dir
    with fastembed_model_load_lock("Qdrant/shared-model"):
        entered.put(os.getpid())
        release.wait(10)


def test_fastembed_model_initialization_is_serialized_across_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    entered = context.Queue()
    release = context.Event()
    processes = [context.Process(target=_hold_model_lock, args=(str(tmp_path), entered, release)) for _ in range(2)]

    try:
        for process in processes:
            process.start()
        first_process_id = entered.get(timeout=10)
        with pytest.raises(queue.Empty):
            entered.get(timeout=0.25)
        release.set()
        second_process_id = entered.get(timeout=10)
    finally:
        release.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert first_process_id != second_process_id
    assert all(process.exitcode == 0 for process in processes)
