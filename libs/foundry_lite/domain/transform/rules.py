"""Pure transform domain rules."""

from __future__ import annotations

from foundry_lite.domain.errors import ValidationFailed

SUPPORTED_TRANSFORM_OUTPUT_MODES = ("snapshot", "append")
SUPPORTED_TRANSFORM_LANGUAGES = ("sql", "python")
TRANSFORM_OUTPUT_MODE_ALIASES = {"incremental": "append"}


def normalize_transform_language(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in SUPPORTED_TRANSFORM_LANGUAGES:
        return normalized
    raise ValidationFailed(
        "unsupported transform language",
        details={"language": language, "supported_languages": list(SUPPORTED_TRANSFORM_LANGUAGES)},
    )


def normalize_transform_output_mode(mode: str) -> str:
    raw = mode.strip().lower()
    normalized = TRANSFORM_OUTPUT_MODE_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_TRANSFORM_OUTPUT_MODES:
        raise ValidationFailed(
            "unsupported transform output mode",
            details={"mode": mode, "supported_modes": ["snapshot", "append", "incremental"]},
        )
    return normalized


def output_transaction_type_for_mode(mode: str) -> str:
    return "APPEND" if normalize_transform_output_mode(mode) == "append" else "SNAPSHOT"


def safe_transform_path_token(value: str, field_name: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("._-")
    if not token:
        raise ValidationFailed(f"transform {field_name} must contain at least one safe character")
    return token
