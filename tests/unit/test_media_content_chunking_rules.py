"""Pure deterministic rules for committed Content Unit chunking."""

from __future__ import annotations

import pytest
from foundry_lite.application.services.media.content_chunking_rules import (
    CONTENT_CHUNK_TOKENIZER_VERSION,
    DEFAULT_CONTENT_CHUNK_OVERLAP,
    DEFAULT_CONTENT_CHUNK_SIZE,
    ContentChunkSpec,
    chunk_text_windows,
    content_chunk_config_hash,
    validate_content_chunk_spec,
)
from foundry_lite.domain.errors import ValidationFailed


def test_default_chunk_contract_is_the_pdf_golden_flow_profile() -> None:
    spec = ContentChunkSpec()

    assert spec.chunk_size == DEFAULT_CONTENT_CHUNK_SIZE == 500
    assert spec.overlap == DEFAULT_CONTENT_CHUNK_OVERLAP == 50
    assert spec.tokenizer_version == CONTENT_CHUNK_TOKENIZER_VERSION == "whitespace_v1"
    assert len(content_chunk_config_hash(spec)) == 64


def test_chunk_windows_are_deterministic_and_apply_exact_overlap() -> None:
    spec = ContentChunkSpec(chunk_size=4, overlap=1)

    first = chunk_text_windows("alpha  beta\ngamma delta epsilon zeta", spec)
    replay = chunk_text_windows("alpha  beta\ngamma delta epsilon zeta", spec)

    assert first == replay
    assert [window.text for window in first] == [
        "alpha beta gamma delta",
        "delta epsilon zeta",
    ]
    assert [(window.start_token, window.end_token) for window in first] == [(0, 4), (3, 6)]
    assert first[0].text_hash != first[1].text_hash


@pytest.mark.parametrize(
    "spec",
    [
        ContentChunkSpec(chunk_size=0, overlap=0),
        ContentChunkSpec(chunk_size=4, overlap=-1),
        ContentChunkSpec(chunk_size=4, overlap=4),
        ContentChunkSpec(chunk_size=4, overlap=1, tokenizer_version="unknown_v1"),
    ],
)
def test_invalid_or_unregistered_settings_fail_without_clamping_or_fallback(spec: ContentChunkSpec) -> None:
    with pytest.raises(ValidationFailed):
        validate_content_chunk_spec(spec)


def test_blank_content_produces_no_synthetic_chunk() -> None:
    assert chunk_text_windows("  \n\t ", ContentChunkSpec()) == ()
