"""Application-layer models and helpers for primitives."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from foundry_lite.domain.errors import InvariantViolation, ValidationFailed

SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INPUT_PATTERN = re.compile(r"\{\{\s*input\('([^']+)'\)\s*\}\}")
MOCK_WRITEBACK_CONNECTOR = "mock_erp_simulator"


@dataclass(frozen=True)
class CommitResult:
    dataset_id: str
    dataset_ref: str
    transaction_id: str
    version_id: str
    version_number: int
    row_count: int
    manifest_uri: str
    schema_hash: str


@dataclass(frozen=True)
class StagedFileStats:
    parquet_path: Path
    row_count: int
    byte_size: int
    content_hash: str
    schema_json: dict[str, object]
    schema_hash: str


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _sql_literal(path: str | Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    if not SQL_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValidationFailed("unsafe SQL identifier", details={"identifier": value})
    return f'"{value}"'


def _json_hash(value: Mapping[str, object]) -> str:
    payload = json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _required_row(row: tuple[object, ...] | None, operation: str) -> tuple[object, ...]:
    if row is None:
        raise InvariantViolation(f"{operation} did not return a row")
    return row


def _json_ready(value: object) -> object:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_manifest_hash(
    *,
    row_count: int,
    byte_size: int,
    content_hash: str,
    schema_hash: str,
) -> str:
    return _json_hash(
        {
            "byte_size": byte_size,
            "content_hash": content_hash,
            "row_count": row_count,
            "schema_hash": schema_hash,
        }
    )


def _dataset_ref_parts(dataset_ref: str) -> tuple[str, str]:
    if "." not in dataset_ref:
        raise ValidationFailed(
            "dataset reference must be namespace.name",
            details={"dataset_ref": dataset_ref},
        )
    namespace, name = dataset_ref.split(".", 1)
    if not namespace or not name:
        raise ValidationFailed(
            "dataset reference must be namespace.name",
            details={"dataset_ref": dataset_ref},
        )
    return namespace, name


def _dataset_ref(row: Mapping[str, object]) -> str:
    return f"{row['namespace']}.{row['name']}"


def _write_rows_to_csv(rows: Sequence[Mapping[str, object]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _normalize_duckdb_type(duckdb_type: str) -> str:
    normalized = duckdb_type.upper()
    if any(token in normalized for token in ["INT", "BIGINT", "HUGEINT", "SMALLINT"]):
        return "integer"
    if any(token in normalized for token in ["DOUBLE", "FLOAT", "DECIMAL", "REAL"]):
        return "float"
    if "BOOL" in normalized:
        return "boolean"
    if "TIME" in normalized or "DATE" in normalized:
        return "timestamp"
    return "string"
