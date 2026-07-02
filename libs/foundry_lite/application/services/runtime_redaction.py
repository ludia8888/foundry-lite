"""Recursive redaction helpers for runtime evidence payloads."""

from __future__ import annotations

from foundry_lite.domain.platform.evidence import redact_evidence


def redact_sensitive(value: object, sensitive: set[str]) -> object:
    return redact_evidence(value, sensitive)
