from __future__ import annotations

import fcntl
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from testcontainers.core.wait_strategies import HttpWaitStrategy
from testcontainers.elasticsearch import ElasticSearchContainer

_ELASTICSEARCH_LIVE_LOCK = "foundry-lite-elasticsearch-live.lock"
ELASTICSEARCH_STARTUP_TIMEOUT_SECONDS = 240


def live_elasticsearch_container(image: str) -> ElasticSearchContainer:
    return ElasticSearchContainer(image).waiting_for(
        HttpWaitStrategy(9200).with_startup_timeout(ELASTICSEARCH_STARTUP_TIMEOUT_SECONDS)
    )


@contextmanager
def live_elasticsearch_lock(lock_path: Path | None = None) -> Iterator[None]:
    path = lock_path or Path(tempfile.gettempdir()) / _ELASTICSEARCH_LIVE_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
