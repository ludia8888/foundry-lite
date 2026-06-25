"""Completion (LLM) model port (Media/Content Plane L13, Palantir OAG query-side techniques).

A completion is a QUERY-SIDE LLM step that runs BEFORE retrieval, never a source truth. It
powers the two query-side retrieval-quality techniques the Palantir OAG page documents:

  * **HyDE (Hypothetical Document Embeddings)** — "instead of embedding the query directly, you
    first ask an LLM to produce a hypothetical chunk that answers this question, which you then
    embed." The hypothetical is search-bait only; real citations still come from retrieved real
    chunks. The HyDE text is embedded with the SAME pinned bge model, so dense retrieval is
    unchanged except for what text we feed it.
  * **Query distillation** — "injecting an LLM step between the user query and what is passed to
    the keyword search allows the possibility of distilling the query" (e.g. extracting the key
    user actions and dropping stop words) to improve the lexical/keyword leg.

The adapter is the seam between the retrieval pipeline and a concrete LLM; the real local LLM is
DEFERRED (much heavier than bge/whisper, a live quality proof needs it) exactly like live-OCR /
live-ASR were contract-first. The default raises; tests inject a deterministic fake.
"""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


class CompletionModelError(Exception):
    """Raised by a completion engine when no model is bundled or the prompt is uncompletable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CompletionModelAdapter(Protocol):
    """Single-prompt completion seam for query-side HyDE / distillation (query-side only)."""

    model_version: str
    is_available: bool

    def complete(self, prompt: str) -> str:
        """Return one completion string for the prompt."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes this adapter promises to report."""
        ...
