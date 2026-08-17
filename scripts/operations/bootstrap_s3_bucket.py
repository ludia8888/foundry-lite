"""Idempotently bootstrap the protected S3 bucket without exposing credentials."""

from __future__ import annotations

import argparse
import json
import re
from typing import Literal
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
from botocore.client import BaseClient  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def ensure_bucket(endpoint: str, bucket: str, *, client: BaseClient | None = None) -> Literal["created", "existing"]:
    _validate_endpoint(endpoint)
    if not _BUCKET_PATTERN.fullmatch(bucket):
        raise ValueError("s3_bucket_name_invalid")
    s3 = client or boto3.client(
        "s3",
        endpoint_url=endpoint,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 3, "mode": "standard"}),
    )
    try:
        s3.head_bucket(Bucket=bucket)
        return "existing"
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status not in {403, 404}:
            raise RuntimeError("s3_bucket_probe_failed") from exc
    try:
        s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        return "created"
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError("s3_bucket_create_failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify a versioned S3 artifact bucket.")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    args = parser.parse_args(argv)
    try:
        status = ensure_bucket(args.endpoint, args.bucket)
    except (RuntimeError, ValueError):
        print(json.dumps({"status": "failed", "reason": "s3_bucket_bootstrap_failed"}))
        return 1
    print(json.dumps({"status": status, "bucket": args.bucket, "isVersioningEnabled": True}, sort_keys=True))
    return 0


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("s3_endpoint_invalid")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("s3_endpoint_invalid")


if __name__ == "__main__":
    raise SystemExit(main())
