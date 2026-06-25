"""Contract for ``CompletionModelAdapter`` (Media/Content Plane L13, Palantir OAG query-side).

A completion is a QUERY-SIDE LLM step that runs before retrieval (HyDE hypothetical + query
distillation), never a source truth. We lock the Protocol shape: ``model_version`` /
``is_available`` attributes, a single ``complete`` returning one string, and a typed
``failure_contract``.
"""

from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.completion_model import CompletionModelAdapter


class _FakeCompletionModel:
    """In-memory implementation pinning the complete/contract shape."""

    model_version = "fake-llm-v1"
    is_available = True

    def complete(self, prompt: str) -> str:
        return f"completion::{prompt}"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile="fake-completion", modes=())


def test_completion_model_single_completion_shape() -> None:
    adapter: CompletionModelAdapter = _FakeCompletionModel()
    completion = adapter.complete("hello")

    assert adapter.is_available is True
    assert adapter.model_version == "fake-llm-v1"
    assert completion == "completion::hello"
    assert adapter.failure_contract().adapter_profile == "fake-completion"
