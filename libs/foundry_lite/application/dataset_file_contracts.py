"""Shared immutable contracts for files committed into a Dataset version."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict

DatasetFilePartitionValues = Mapping[str, object]


@dataclass(frozen=True)
class DatasetFileRecord:
    file_id: str
    tenant_id: str
    dataset_version_id: str
    uri: str
    file_format: str
    row_count: int
    byte_size: int
    content_hash: str
    partition_values: DatasetFilePartitionValues


class DatasetFileRow(TypedDict):
    id: str
    tenant_id: str
    dataset_version_id: str
    uri: str
    format: str
    row_count: int
    byte_size: int
    content_hash: str
    partition_values: DatasetFilePartitionValues
