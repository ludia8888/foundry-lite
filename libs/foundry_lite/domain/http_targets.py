"""Pure target-shape rules shared by connector configuration and delivery."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


def _clean_http_text(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _split_http_text(value: object, *, should_validate_port: bool) -> tuple[str, SplitResult] | None:
    text = _clean_http_text(value)
    if text is None:
        return None
    try:
        parsed = urlsplit(text)
        if should_validate_port:
            _ = parsed.port
    except ValueError:
        return None
    return text, parsed


def is_relative_http_resource_path(value: object) -> bool:
    """Return whether a resource stays subordinate to its registered base URL."""

    result = _split_http_text(value, should_validate_port=False)
    if result is None:
        return False
    text, parsed = result
    return not parsed.scheme and not parsed.netloc and not parsed.fragment and not text.startswith("//")


def is_safe_http_base_url(value: object) -> bool:
    """Accept an absolute HTTP origin/path without inline secrets or URL suffix state."""

    result = _split_http_text(value, should_validate_port=True)
    if result is None:
        return False
    _, parsed = result
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


__all__ = ["is_relative_http_resource_path", "is_safe_http_base_url"]
