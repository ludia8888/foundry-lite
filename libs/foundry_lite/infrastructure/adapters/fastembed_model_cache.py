"""Process-safe FastEmbed model-cache initialization."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

_MODEL_LOAD_TIMEOUT_SECONDS = 600


def _cache_dir() -> Path:
    configured = os.environ.get("FASTEMBED_CACHE_PATH")
    cache_dir = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "fastembed_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@contextmanager
def fastembed_model_load_lock(model_name: str) -> Iterator[Path]:
    """Serialize one model's download and ONNX session construction across workers."""

    cache_dir = _cache_dir()
    lock_dir = cache_dir / ".foundry-lite-model-load-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    model_key = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:20]
    with FileLock(lock_dir / f"{model_key}.lock", timeout=_MODEL_LOAD_TIMEOUT_SECONDS):
        yield cache_dir
