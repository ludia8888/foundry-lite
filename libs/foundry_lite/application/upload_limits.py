from __future__ import annotations

import os
from pathlib import Path

from foundry_lite.domain.errors import ValidationFailed

DEFAULT_MAX_CSV_UPLOAD_BYTES = 50 * 1024 * 1024
CSV_UPLOAD_LIMIT_ENV = "FOUNDRY_LITE_MAX_CSV_UPLOAD_BYTES"


def require_csv_size_limit(source_path: Path) -> None:
    max_bytes = int(os.getenv(CSV_UPLOAD_LIMIT_ENV, str(DEFAULT_MAX_CSV_UPLOAD_BYTES)))
    size = source_path.stat().st_size
    if size > max_bytes:
        raise ValidationFailed(
            "csv file exceeds configured size limit",
            details={"path": str(source_path), "size_bytes": size, "max_bytes": max_bytes},
        )
