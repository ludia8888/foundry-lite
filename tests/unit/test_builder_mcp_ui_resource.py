"""Hosted confirmation resources must not reuse a stale asset cache key."""

from __future__ import annotations

import hashlib

from foundry_lite_api.builder_mcp_ui import (
    BUILDER_CONFIRMATION_RESOURCE_URI,
    builder_resource_descriptor,
    read_builder_resource,
)


def test_builder_confirmation_uri_matches_asset_hash_and_preserves_old_conversations() -> None:
    result = read_builder_resource({"uri": BUILDER_CONFIRMATION_RESOURCE_URI})
    html = result["contents"][0]["text"]
    digest = hashlib.sha256(html.encode()).hexdigest()[:12]
    assert BUILDER_CONFIRMATION_RESOURCE_URI.endswith(f"-{digest}.html")
    assert builder_resource_descriptor()["uri"] == BUILDER_CONFIRMATION_RESOURCE_URI
    legacy = read_builder_resource({"uri": "ui://foundry-lite/builder-confirmation-v1.html"})
    assert legacy["contents"][0]["text"] == html
