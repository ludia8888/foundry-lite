from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.operations import switch_macmini_external_oidc as subject


def _args(root: Path) -> Namespace:
    chart = root / "repo/deploy/helm/foundry-lite"
    chart.mkdir(parents=True)
    kubeconfig = root / "state/kubeconfig"
    kubeconfig.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig.write_text("config", encoding="utf-8")
    return Namespace(
        run_id="run-1",
        namespace="foundry-qa",
        kubeconfig=str(kubeconfig),
        kubectl="kubectl",
        helm="helm",
        chart=str(chart),
        public_base_url="https://foundry.example.test",
        identity_base_url="https://identity.example.test",
        application_id="foundry-lite",
        allowed_client_id=["chatgpt-client"],
    )


def _embedded_values() -> dict[str, object]:
    return {
        "global": {"runtimeProfile": "test"},
        "auth": {"profile": "header-trust"},
        "external": {"oidc": {"discoveryUrl": ""}},
    }


def _oidc_values(desired: dict[str, object]) -> dict[str, object]:
    return {"global": {"runtimeProfile": "test"}, **desired}


def test_switch_performs_one_atomic_upgrade_then_reconciles_exact_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    desired = subject._desired_values(
        "https://foundry.example.test",
        "https://identity.example.test",
        "foundry-lite",
        frozenset({"chatgpt-client"}),
    )
    responses = iter((_embedded_values(), _oidc_values(desired)))
    upgrades: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        if command[1:3] == ("get", "values"):
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(next(responses)).encode(), stderr=b"")
        if command[1] == "upgrade":
            upgrades.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda namespace: None)
    monkeypatch.setattr(subject, "utc_now", lambda: "2026-08-18T00:00:00Z")
    monkeypatch.setattr(subject.subprocess, "run", run)
    monkeypatch.setattr(
        subject,
        "write_json_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )

    receipt = subject.switch(args)

    assert receipt["phase"] == "upgraded"
    assert len(upgrades) == 1
    command = upgrades[0]
    assert "--atomic" in command
    assert "--reset-then-reuse-values" in command
    assert "--reuse-values" not in command
    assert command[command.index("--namespace") + 1] == "foundry-qa"
    override = tmp_path / "state/run-1-external-oidc.json"
    assert override.stat().st_mode & 0o077 == 0
    assert json.loads(override.read_text(encoding="utf-8")) == desired
    assert "global" not in desired
    assert "secrets" not in desired
    assert receipt["runtimeProfile"] == "test"
    assert receipt["runtimeProfilePreserved"] is True
    assert receipt["authAdapter"] == "strict-external-jwt-oidc"


def test_switch_does_not_blind_retry_an_already_reconciled_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    desired = subject._desired_values(
        "https://foundry.example.test",
        "https://identity.example.test",
        "foundry-lite",
        frozenset({"chatgpt-client"}),
    )
    upgrades: list[object] = []

    def run(command, **_kwargs):
        if command[1:3] == ("get", "values"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(_oidc_values(desired)).encode(),
                stderr=b"",
            )
        if command[1] == "upgrade":
            upgrades.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda namespace: None)
    monkeypatch.setattr(subject.subprocess, "run", run)
    monkeypatch.setattr(subject, "write_json_receipt", lambda _path, _payload: None)

    receipt = subject.switch(args)

    assert receipt["phase"] == "already_configured"
    assert upgrades == []


def test_switch_rejects_unexpected_partial_auth_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = _embedded_values()
    partial["auth"] = {"profile": "oidc"}
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda namespace: None)
    desired = subject._desired_values(
        "https://foundry.example.test",
        "https://identity.example.test",
        "foundry-lite",
        frozenset({"chatgpt-client"}),
    )

    with pytest.raises(RuntimeError, match="source_profile_unexpected"):
        subject._require_switchable_source(partial, desired)


def test_switch_allows_client_rotation_only_on_same_external_oidc_coordinates() -> None:
    desired = subject._desired_values(
        "https://foundry.example.test",
        "https://identity.example.test",
        "foundry-lite",
        frozenset({"new-client"}),
    )
    current = _oidc_values(
        subject._desired_values(
            "https://foundry.example.test",
            "https://identity.example.test",
            "foundry-lite",
            frozenset({"old-client"}),
        )
    )

    subject._require_switchable_source(current, desired)

    other_issuer = _oidc_values(
        subject._desired_values(
            "https://foundry.example.test",
            "https://other-identity.example.test",
            "foundry-lite",
            frozenset({"old-client"}),
        )
    )
    with pytest.raises(RuntimeError, match="source_profile_unexpected"):
        subject._require_switchable_source(other_issuer, desired)
