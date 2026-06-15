from __future__ import annotations

import re

from foundry_lite.domain.errors import ValidationFailed

_RAW_FILE_FUNCTION_PATTERN = re.compile(
    r"\b(read_csv|read_csv_auto|read_json|read_json_auto|read_parquet|parquet_scan|read_blob)\s*\(",
    re.IGNORECASE,
)
_LINE_COMMENT_PATTERN = re.compile(r"--.*?$", re.MULTILINE)
_BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)


def validate_sql_transform_uses_declared_inputs(sql: str) -> None:
    match = _RAW_FILE_FUNCTION_PATTERN.search(_strip_sql_comments(sql))
    if match is None:
        return
    raise ValidationFailed(
        "transform SQL must use declared input datasets instead of raw filesystem reads",
        details={"function": match.group(1).lower()},
    )


def _strip_sql_comments(sql: str) -> str:
    without_blocks = _BLOCK_COMMENT_PATTERN.sub("", sql)
    return _LINE_COMMENT_PATTERN.sub("", without_blocks)
