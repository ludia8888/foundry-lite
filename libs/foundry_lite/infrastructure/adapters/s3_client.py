"""Shared S3 client construction and error classification for S3-backed adapters.

Both the dataset and media S3 adapters talk to the same S3/MinIO surface, so the
boto3 client builder, the missing-object classifier, and key joining live here
rather than being duplicated per adapter. boto3 is imported lazily so S3 stays an
optional dependency for local/test runs.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_NOT_FOUND_ERROR_CODES = {"NoSuchKey", "NoSuchBucket", "NoSuchUpload", "404", "NotFound"}
_PRECONDITION_ERROR_CODES = {"ConditionalRequestConflict", "PreconditionFailed", "409", "412"}


def build_s3_client(
    *,
    endpoint_url: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    region_name: str,
) -> Any:
    boto3 = import_module("boto3")
    botocore_config = import_module("botocore.config")
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region_name,
        config=botocore_config.Config(s3={"addressing_style": "path"}),
    )


def is_not_found_error(exc: Exception) -> bool:
    """Classify a boto3/botocore error as a genuine missing-object (vs transient)."""
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code", ""))
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if code in _NOT_FOUND_ERROR_CODES or status == 404:
            return True
    return False


def is_precondition_failed_error(exc: Exception) -> bool:
    """Classify an immutable S3 conditional-write collision."""

    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code", ""))
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return code in _PRECONDITION_ERROR_CODES or status in {409, 412}
    return False


def join_key(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part.strip("/"))
