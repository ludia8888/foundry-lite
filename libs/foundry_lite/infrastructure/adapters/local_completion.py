"""Local completion (LLM) model adapter (Media/Content Plane L13).

The completion engine is injectable — the default raises ``completion_model_unavailable``
because no LLM is bundled. A real local LLM is much heavier than bge/whisper and a live quality
proof needs it, so the live engine is DEFERRED exactly like live-OCR / live-ASR were
contract-first. ``is_available`` reflects whether a real engine was injected; when it is False
the retrieval services stay byte-for-byte unchanged (no HyDE, no distillation — they embed the
raw query and keyword-search the raw text). Tests inject a deterministic pure-python fake, so no
heavy ML dependency enters CI and there is no new model in CI.
"""

from __future__ import annotations

from collections.abc import Callable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.completion_model import CompletionModelError

CompletionEngine = Callable[[str], str]

_DEFAULT_MODEL_VERSION = "local-completion-v0"


def _default_completion_engine(prompt: str) -> str:
    # No LLM is bundled by default; a real profile injects one. The injectable seam is what L13
    # proves; the live local-LLM engine is a future section (like live-OCR / live-ASR were).
    del prompt
    raise CompletionModelError("completion_model_unavailable")


class LocalCompletionAdapter:
    """``CompletionModelAdapter`` whose engine is injected (profile ``local-completion``)."""

    profile_name = "local-completion"

    def __init__(
        self, *, completion_engine: CompletionEngine | None = None, model_version: str = _DEFAULT_MODEL_VERSION
    ) -> None:
        self._completion_engine = completion_engine or _default_completion_engine
        self.is_available = completion_engine is not None
        self.model_version = model_version

    def complete(self, prompt: str) -> str:
        return self._completion_engine(prompt)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "complete",
                    "unavailable",
                    True,
                    "No completion model is available; a real completion model profile must be configured.",
                ),
            ),
        )
