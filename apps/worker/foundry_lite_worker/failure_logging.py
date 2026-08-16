"""Secret-safe structured logging owned by worker process entrypoints."""

from __future__ import annotations

import json
import logging


def log_tick_failure(
    logger: logging.Logger,
    event: str,
    *,
    request_id: str,
    exc: Exception,
) -> None:
    """Log only stable coordinates and exception type, never exception text or traceback."""

    logger.error(
        json.dumps(
            {
                "event": event,
                "exception_type": type(exc).__name__,
                "request_id": request_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


__all__ = ["log_tick_failure"]
