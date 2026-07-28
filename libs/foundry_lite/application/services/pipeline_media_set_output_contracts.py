"""Typed contracts and failures for the Pipeline Graph v2 Media Set output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


class PipelineMediaSetOutputContractMismatch(ValidationFailed):
    """The configured target Media Set cannot safely receive the input."""

    code = "PIPELINE_MEDIA_SET_OUTPUT_CONTRACT_MISMATCH"


class PipelineMediaDerivativeBytesUnavailable(ValidationFailed):
    """A committed derivative row has no verifiable durable byte artifact."""

    code = "PIPELINE_MEDIA_DERIVATIVE_BYTES_UNAVAILABLE"


class PipelineMediaDerivativeSecurityMismatch(ValidationFailed):
    """A derivative weakened or replaced its committed source security controls."""

    code = "PIPELINE_MEDIA_DERIVATIVE_SECURITY_MISMATCH"


class PipelineMediaOutputSourceCorrupt(ValidationFailed):
    """A committed source coordinate does not match its durable bytes."""

    code = "PIPELINE_MEDIA_OUTPUT_SOURCE_CORRUPT"


@dataclass(frozen=True, slots=True)
class MediaOutputEntry:
    """One immutable input byte object to copy into the serving Media Set."""

    entry_fingerprint: str
    source_media_item_version_id: str
    media_derivative_id: str | None
    logical_path: str
    blob_key: str
    content_hash: str
    byte_size: int
    mime_type: str
    schema_type: str
    format: str
    primary_format: str
    allowed_formats: tuple[str, ...]
    security_envelope: JsonObject


@dataclass(frozen=True, slots=True)
class MediaOutputContract:
    """Target Media Set contract inferred from committed input truth."""

    schema_type: str
    primary_format: str
    allowed_formats: tuple[str, ...]
    classification: str


@dataclass(frozen=True, slots=True)
class MediaOutputTransactionAttempt:
    """One fenced transaction generation for a run/node output attempt."""

    media_transaction_id: str
    generation: int
    transaction_status: Literal["OPEN", "COMMITTED"]


def normalized_classification(value: object) -> str:
    """Return the canonical classification label used by media contracts."""

    return value.strip().upper() if isinstance(value, str) and value.strip() else "UNCLASSIFIED"
