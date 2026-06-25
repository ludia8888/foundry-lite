"""Provider-compatible-proxy ``LanguageModelAdapter`` (AIP-lite P0b — real path, DEFERRED).

Mirrors Palantir's provider-compatible proxy: the app authenticates with a FOUNDRY TOKEN (never a
provider API key) and the request binds to a concrete Catalog model; no provider SDK is imported in
app logic (palantir.com/docs/foundry/aip/llm-provider-compatible-apis). The live engine needs a
Foundry token + network, so it is DEFERRED exactly like the live-OCR/live-ASR/local-LLM engines:
the default raises ``language_model_unavailable``. The seam + contract is what P0b proves; a real
profile injects an ``engine``.

Credentials are referenced via ``SecretProvider`` by NAME/version; the raw token never enters this
object, the response, or any trace — only ``SecretValue.redacted()`` (name + version) is exposed.
"""

from __future__ import annotations

from collections.abc import Callable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.language_model import (
    LanguageModelError,
    ModelEvent,
    ModelRequest,
    ModelResponse,
)
from foundry_lite.application.ports.secret_provider import SecretProvider

LanguageModelEngine = Callable[[ModelRequest], ModelResponse]

# The secret NAME the SecretProvider resolves by (not a credential value — the raw token never
# lives here; only the name/version is referenced, mirroring the SecretProvider contract).
_DEFAULT_FOUNDRY_TOKEN_SECRET = "foundry_aip_token"  # nosec B105


def _default_language_model_engine(request: ModelRequest) -> ModelResponse:
    # No live proxy is bundled by default; a real profile injects an engine. The injectable seam +
    # contract is what P0b proves (the live proxy needs a Foundry token + network, so it is DEFERRED
    # like live-OCR / live-ASR / the local LLM were contract-first).
    del request
    raise LanguageModelError("language_model_unavailable")


class ProviderCompatibleLanguageModel:
    """``LanguageModelAdapter`` for the provider-compatible proxy (profile ``provider-compatible``)."""

    profile_name = "provider-compatible"

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        engine: LanguageModelEngine | None = None,
        foundry_token_secret_name: str = _DEFAULT_FOUNDRY_TOKEN_SECRET,
    ) -> None:
        self._secret_provider = secret_provider
        self._engine = engine or _default_language_model_engine
        self._foundry_token_secret_name = foundry_token_secret_name
        self.is_available = engine is not None

    def complete(self, request: ModelRequest) -> ModelResponse:
        # Resolve the Foundry token by NAME/version only; the raw value never leaves SecretProvider.
        self._secret_provider.get_secret(self._foundry_token_secret_name)
        return self._engine(request)

    def stream(self, request: ModelRequest) -> list[ModelEvent]:
        # Streaming over the real proxy is DEFERRED like ``complete``: the default engine raises
        # ``language_model_unavailable`` (a real profile injects a streaming-capable engine).
        del request
        raise LanguageModelError("language_model_unavailable")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "complete",
                    "unavailable",
                    True,
                    "No provider-compatible language model is available; a real proxy profile must be configured.",
                ),
            ),
        )
