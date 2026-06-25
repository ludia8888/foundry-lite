"""Query-side LLM enrichment for retrieval (L13 — Palantir OAG HyDE + query distillation).

These are the QUERY-SIDE retrieval-quality techniques the OAG page documents, run BEFORE
retrieval. They are the query-side LLM techniques we still lacked — RRF (the documented OAG
"reranking" / fused ``rerankedResults``) is already the fusion step and a post-retrieval
cross-encoder is intentionally beyond Palantir (BYO, not built here).

  * **HyDE**: when a completion model is wired, embed an LLM-produced *hypothetical answer* to the
    question instead of the short question itself — the hypothetical is search-bait only (real
    citations still come from the retrieved real chunks). It is embedded with the SAME pinned bge
    model, so dense retrieval is unchanged except for the text fed to it.
  * **Query distillation**: when a completion model is wired, distill the query into cleaned
    keyword terms for the lexical/keyword leg.

This is DEFAULT-OFF: when the completion model is not available (the default), every helper
returns the raw query text, so retrieval behaves byte-for-byte as before.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.completion_model import CompletionModelAdapter

HYDE_PROMPT = (
    "Write a short factual passage that directly answers the following question, as if it were an "
    "excerpt from an authoritative document. Do not restate the question.\n\nQuestion: {query}\n\nPassage:"
)
DISTILL_PROMPT = (
    "Extract the key user actions and entities from the given query as space-separated keyword "
    "terms, removing unnecessary stop words. Return only the terms.\n\nQuery: {query}\n\nTerms:"
)


def hyde_prompt(query_text: str) -> str:
    """Prompt that asks the LLM for a hypothetical answer passage (HyDE search-bait)."""
    return HYDE_PROMPT.format(query=query_text)


def distill_prompt(query_text: str) -> str:
    """Prompt that asks the LLM to distill the query into cleaned keyword terms."""
    return DISTILL_PROMPT.format(query=query_text)


@dataclass(frozen=True)
class EnrichedQuery:
    """The two query-side texts retrieval consumes: dense-vector source + keyword text."""

    dense_text: str
    keyword_text: str


def enrich_query(query_text: str, completion_model_adapter: CompletionModelAdapter) -> EnrichedQuery:
    """Apply HyDE + distillation when a completion model is wired; else return the raw query.

    Returns the text to embed for the dense leg (the HyDE hypothetical, or the raw query) and the
    text for the keyword leg (the distilled terms, or the raw query). The hypothetical is
    search-bait — only the text fed to the existing bge embedder changes, never the citations.
    """
    if not completion_model_adapter.is_available or not query_text:
        return EnrichedQuery(dense_text=query_text, keyword_text=query_text)
    hypothetical = completion_model_adapter.complete(hyde_prompt(query_text)).strip()
    distilled = completion_model_adapter.complete(distill_prompt(query_text)).strip()
    return EnrichedQuery(
        dense_text=hypothetical or query_text,
        keyword_text=distilled or query_text,
    )
