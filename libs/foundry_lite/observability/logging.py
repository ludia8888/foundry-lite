"""Observability helpers for logging."""

from __future__ import annotations

import json
import logging
from typing import Any

TRACE_FIELD_NAMES = {
    "request_id",
    "tenant_id",
    "run_id",
    "trace_id",
    "correlation_id",
    "sync_run_id",
    "transform_run_id",
    "index_run_id",
    "action_run_id",
    "materialization_run_id",
    "dataset_version_id",
    "transaction_id",
    "object_id",
}


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(message)s")


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    if not any(fields.get(name) for name in TRACE_FIELD_NAMES):
        raise ValueError("log_event requires request_id, tenant_id, or a run_id-family field")
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))
