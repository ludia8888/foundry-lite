from __future__ import annotations

import pytest
from foundry_lite.domain.http_headers import is_safe_custom_header_name, is_safe_http_header_value
from foundry_lite.domain.http_targets import is_relative_http_resource_path, is_safe_http_base_url


@pytest.mark.parametrize("name", ["Authorization", "host", "Content-Length", "bad header", "X-Test\r\nInjected"])
def test_custom_header_name_rejects_runtime_owned_or_malformed_names(name: str) -> None:
    assert not is_safe_custom_header_name(name)


def test_custom_header_and_secret_value_accept_safe_text_only() -> None:
    assert is_safe_custom_header_name("X-Provider-Key")
    assert is_safe_http_header_value("secret value")
    assert not is_safe_http_header_value("")
    assert not is_safe_http_header_value("secret\r\nInjected: true")
    assert not is_safe_http_header_value("secret\x7f")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/api",
        "https://user:secret@example.com/api",
        "https://example.com/api?token=secret",
        "https://example.com/api#fragment",
        " https://example.com/api",
        "https://example.com:invalid/api",
    ],
)
def test_base_url_rejects_unsafe_or_ambiguous_targets(url: str) -> None:
    assert not is_safe_http_base_url(url)


def test_registered_base_and_relative_resource_cannot_change_origin() -> None:
    assert is_safe_http_base_url("https://api.example.com/v1")
    assert is_relative_http_resource_path("orders/O-1?expand=items")
    assert not is_relative_http_resource_path("https://evil.example/orders")
    assert not is_relative_http_resource_path("//evil.example/orders")
    assert not is_relative_http_resource_path("..\\evil")
    assert not is_relative_http_resource_path("orders#fragment")
