"""Application-layer models and helpers for upload limits."""

from __future__ import annotations

import os
from pathlib import Path

from foundry_lite.domain.errors import ValidationFailed

DEFAULT_MAX_CSV_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_WEBHOOK_BODY_BYTES = 1 * 1024 * 1024
CSV_UPLOAD_LIMIT_ENV = "FOUNDRY_LITE_MAX_CSV_UPLOAD_BYTES"
WEBHOOK_BODY_LIMIT_ENV = "FOUNDRY_LITE_MAX_WEBHOOK_BODY_BYTES"


def require_csv_size_limit(source_path: Path) -> None:
    max_bytes = int(os.getenv(CSV_UPLOAD_LIMIT_ENV, str(DEFAULT_MAX_CSV_UPLOAD_BYTES)))
    size = source_path.stat().st_size
    if size > max_bytes:
        raise ValidationFailed(
            "csv file exceeds configured size limit",
            details={"path": str(source_path), "size_bytes": size, "max_bytes": max_bytes},
        )


def max_webhook_body_bytes() -> int:
    configured = os.getenv(WEBHOOK_BODY_LIMIT_ENV, str(DEFAULT_MAX_WEBHOOK_BODY_BYTES))
    try:
        max_bytes = int(configured)
    except ValueError as exc:
        raise ValidationFailed(
            "webhook body size limit must be an integer",
            details={"env": WEBHOOK_BODY_LIMIT_ENV},
        ) from exc
    if max_bytes <= 0:
        raise ValidationFailed(
            "webhook body size limit must be positive",
            details={"env": WEBHOOK_BODY_LIMIT_ENV, "max_bytes": max_bytes},
        )
    return max_bytes
