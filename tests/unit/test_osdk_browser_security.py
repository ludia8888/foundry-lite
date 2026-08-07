from __future__ import annotations

from pathlib import Path

from foundry_lite_api import main as api_main

ROOT = Path(__file__).resolve().parents[2]


def test_osdk_browser_security_cors_and_websocket_origin_allowlist_is_explicit() -> None:
    origins = set(api_main.ALLOWED_BROWSER_ORIGINS)

    assert origins == {"http://127.0.0.1:4173", "http://localhost:4173"}
    assert "*" not in origins


def test_osdk_browser_security_generated_sdk_does_not_persist_tokens_or_use_raw_html_sinks() -> None:
    generated = (ROOT / "packages/sdk-ts/src/generated.ts").read_text()
    forbidden_sinks = (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "dangerouslySetInnerHTML",
        "credentials: 'include'",
        'credentials: "include"',
    )

    for sink in forbidden_sinks:
        assert sink not in generated


def test_osdk_browser_security_threat_model_documents_current_and_future_boundaries() -> None:
    threat_model = (ROOT / "docs/osdk-security-threat-model.md").read_text()

    assert "Current Mitigations" in threat_model
    assert "Current Non-Goals" in threat_model
    assert "Future Controls" in threat_model
    assert "at-least-once" in threat_model
    assert "Exactly-once remains future" in threat_model
    assert "SameSite cookies and CSRF tokens" in threat_model
    assert "localStorage" in threat_model
    assert "WebSocket Origin" in threat_model
