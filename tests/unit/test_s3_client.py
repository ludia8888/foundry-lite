"""Unit cover for the shared S3 client helpers (error classification + key joining)."""

from __future__ import annotations

from foundry_lite.infrastructure.adapters.s3_client import is_not_found_error, join_key


class _ClientError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


def test_is_not_found_error_classifies_missing_objects() -> None:
    assert is_not_found_error(FileNotFoundError("gone")) is True
    assert is_not_found_error(_ClientError("NoSuchKey", 404)) is True
    assert is_not_found_error(_ClientError("AccessDenied", 403)) is False
    assert is_not_found_error(RuntimeError("transient")) is False


def test_join_key_trims_and_skips_empty_segments() -> None:
    assert join_key("foundry-lite", "tenant", "media") == "foundry-lite/tenant/media"
    assert join_key("/a/", "", "/b/") == "a/b"
