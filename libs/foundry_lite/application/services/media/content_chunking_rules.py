"""Deterministic rules for committed Content Unit chunking."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.primitives import _json_hash
from foundry_lite.domain.errors import ValidationFailed

DEFAULT_CONTENT_CHUNK_SIZE = 500
DEFAULT_CONTENT_CHUNK_OVERLAP = 50
CONTENT_CHUNK_PROCESSOR = "content_chunk_v1"
CONTENT_CHUNK_PROCESSOR_VERSION = "1.0.0"
CONTENT_CHUNK_TOKENIZER_VERSION = "whitespace_v1"
CONTENT_CHUNK_DERIVATIVE_KIND = "content_chunks"
_MAX_CHUNK_SIZE = 100_000


@dataclass(frozen=True, slots=True)
class ContentChunkSpec:
    """Pinned deterministic chunk settings used by Pipeline Graph v2."""

    chunk_size: int = DEFAULT_CONTENT_CHUNK_SIZE
    overlap: int = DEFAULT_CONTENT_CHUNK_OVERLAP
    tokenizer_version: str = CONTENT_CHUNK_TOKENIZER_VERSION


@dataclass(frozen=True, slots=True)
class ContentChunkWindow:
    """One stable whitespace-token window within a parent Content Unit."""

    local_ordinal: int
    start_token: int
    end_token: int
    text: str
    text_hash: str


def validate_content_chunk_spec(spec: ContentChunkSpec) -> None:
    """Reject unsupported or ambiguous settings instead of applying a fallback."""

    if not isinstance(spec.chunk_size, int) or isinstance(spec.chunk_size, bool):
        raise ValidationFailed("content chunk size must be an integer")
    if spec.chunk_size < 1 or spec.chunk_size > _MAX_CHUNK_SIZE:
        raise ValidationFailed(
            "content chunk size is outside the supported range",
            details={"chunkSize": spec.chunk_size, "maximum": _MAX_CHUNK_SIZE},
        )
    if not isinstance(spec.overlap, int) or isinstance(spec.overlap, bool):
        raise ValidationFailed("content chunk overlap must be an integer")
    if spec.overlap < 0 or spec.overlap >= spec.chunk_size:
        raise ValidationFailed(
            "content chunk overlap must be non-negative and smaller than chunk size",
            details={"chunkSize": spec.chunk_size, "overlap": spec.overlap},
        )
    if spec.tokenizer_version != CONTENT_CHUNK_TOKENIZER_VERSION:
        raise ValidationFailed(
            "content chunk tokenizer version is not supported",
            details={
                "tokenizerVersion": spec.tokenizer_version,
                "supportedTokenizerVersion": CONTENT_CHUNK_TOKENIZER_VERSION,
            },
        )


def content_chunk_config_hash(spec: ContentChunkSpec) -> str:
    """Hash the exact algorithm identity independently from its input set."""

    validate_content_chunk_spec(spec)
    return _json_hash(
        {
            "processor": CONTENT_CHUNK_PROCESSOR,
            "processorVersion": CONTENT_CHUNK_PROCESSOR_VERSION,
            "tokenizerVersion": spec.tokenizer_version,
            "chunkSize": spec.chunk_size,
            "overlap": spec.overlap,
        }
    )


def chunk_text_windows(text: str, spec: ContentChunkSpec) -> tuple[ContentChunkWindow, ...]:
    """Split text into stable whitespace-token windows with explicit overlap."""

    validate_content_chunk_spec(spec)
    tokens = text.split()
    if not tokens:
        return ()
    step = spec.chunk_size - spec.overlap
    windows: list[ContentChunkWindow] = []
    for ordinal, start in enumerate(range(0, len(tokens), step)):
        end = min(start + spec.chunk_size, len(tokens))
        chunk_text = " ".join(tokens[start:end])
        windows.append(
            ContentChunkWindow(
                local_ordinal=ordinal,
                start_token=start,
                end_token=end,
                text=chunk_text,
                text_hash=_json_hash({"text": chunk_text}),
            )
        )
        if end == len(tokens):
            break
    return tuple(windows)
