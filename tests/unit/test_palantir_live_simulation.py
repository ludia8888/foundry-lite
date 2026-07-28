from __future__ import annotations

import json
from pathlib import Path

from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse
from pytest import MonkeyPatch

from scripts.operations import run_palantir_live_simulation as simulation


class _StubAnthropicLanguageModel:
    profile_name = "anthropic"
    supported_provider_profiles = ("anthropic",)
    routed_requests: list[ModelRequest] = []

    def __init__(self, _secret_provider: object) -> None:
        self.routed_requests.clear()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.routed_requests.append(request)
        return ModelResponse(
            provider="anthropic",
            resolved_model_id="",
            resolved_model_revision="",
            content="The public source counts are usable for a bounded operator smoke test.",
            finish_reason="end_turn",
            input_tokens=20,
            output_tokens=12,
            provider_request_id="request-test",
            latency_ms=5,
        )


def _config() -> simulation.SimulationConfig:
    return simulation.SimulationConfig(
        output=Path("unused.json"),
        source_limit=1,
        command_timeout_seconds=60,
        is_gate_run_enabled=False,
        is_cdc_enabled=False,
        is_media_bytes_enabled=False,
        is_live_llm_enabled=True,
        media_profile="none",
        live_llm_model=simulation.DEFAULT_LIVE_LLM_MODEL,
        live_llm_secret_name=simulation.DEFAULT_LIVE_LLM_SECRET_NAME,
        live_llm_timeout_seconds=10,
    )


def test_live_llm_probe_uses_governed_anthropic_route(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(simulation, "AnthropicLanguageModel", _StubAnthropicLanguageModel)

    probe = simulation._probe_live_llm_gateway(_config(), ())

    assert probe.status == "passed"
    assert probe.details["provider"] == "anthropic"
    assert probe.details["model"] == "claude-sonnet-5"
    assert probe.details["secretRef"] == "anthropic_api_key"
    [request] = _StubAnthropicLanguageModel.routed_requests
    assert request.environment == "operator-live"
    assert request.resolved_route is not None
    assert request.resolved_route.provider_profile == "anthropic"
    assert request.resolved_route.provider_model_id == "claude-sonnet-5"
    assert request.resolved_route.secret_ref == "anthropic_api_key"


def test_live_llm_defaults_use_current_model_and_deduplicated_env_vars() -> None:
    assert simulation.DEFAULT_LIVE_LLM_MODEL == "claude-sonnet-5"
    assert simulation.DEFAULT_LIVE_LLM_SECRET_NAME == "anthropic_api_key"
    assert simulation._live_llm_env_vars("anthropic_api_key") == (
        "FOUNDRY_LITE_SECRET_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
    )


def test_provider_error_evidence_redacts_sensitive_details_and_raw_exceptions() -> None:
    credential = "test-provider-credential"
    adapter_error = AdapterError(
        AdapterFailure(
            adapter_profile="anthropic",
            operation="complete",
            kind="authentication",
            is_retryable=False,
            operator_message="Anthropic credential was rejected.",
            details={"apiKey": credential, "requestId": "request-safe"},
        )
    )

    typed_payload = simulation._safe_provider_error(adapter_error)
    unexpected_payload = simulation._safe_provider_error(RuntimeError(f"provider echoed {credential}"))
    evidence = json.dumps({"typed": typed_payload, "unexpected": unexpected_payload})

    assert credential not in evidence
    assert typed_payload["kind"] == "authentication"
    assert typed_payload["details"] == {"apiKey": "***REDACTED***", "requestId": "request-safe"}
    assert unexpected_payload["message"] == "Live provider probe failed; raw exception text was redacted."
