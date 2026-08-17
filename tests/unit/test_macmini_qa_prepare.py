from __future__ import annotations

from pathlib import Path

import pytest

from scripts.operations import prepare_macmini_qa
from scripts.operations.macmini_qa_guard import CommandResult


def _result(argv: tuple[str, ...], *, stdout: str = "", return_code: int = 0) -> CommandResult:
    return CommandResult(argv=argv, return_code=return_code, stdout=stdout, stderr="")


def test_k3s_readiness_poll_is_bounded_and_waits_for_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter((_result(("k3s",), return_code=1), _result(("k3s",), stdout="ok\n")))
    waits: list[int] = []
    monkeypatch.setattr(prepare_macmini_qa, "run", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(prepare_macmini_qa.time, "sleep", waits.append)

    ready = prepare_macmini_qa._wait_for_k3s("foundry-qa", attempts=2)

    assert ready.stdout == "ok\n"
    assert waits == [2]


def test_secret_encryption_uses_k3s_configured_server_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        commands.append(argv)
        if "awk" in argv:
            return _result(argv, stdout="https://127.0.0.1:63861\n")
        return _result(argv, stdout='{"enable":true,"hashmatch":true,"stage":"start"}\n')

    monkeypatch.setattr(prepare_macmini_qa, "run", fake_run)

    status = prepare_macmini_qa._secrets_encryption_status("foundry-qa")

    assert status["enable"] is True
    assert commands[1][-4:] == ("--server", "https://127.0.0.1:63861", "--output", "json")


def test_secret_encryption_rejects_non_loopback_k3s_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_macmini_qa,
        "run",
        lambda argv, **_kwargs: _result(argv, stdout="https://other-host:6443\n"),
    )

    with pytest.raises(RuntimeError, match="server_url_invalid"):
        prepare_macmini_qa._secrets_encryption_status("foundry-qa")


def test_kubeconfig_is_private_validated_and_installed_inside_qa_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qa_root = tmp_path / "foundry-qa"
    (qa_root / "state").mkdir(parents=True)
    commands: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        commands.append(argv)
        if argv[:3] == ("kubectl", "config", "view"):
            return _result(argv, stdout="apiVersion: v1\ncurrent-context: colima-foundry-qa\n")
        return _result(argv, stdout="ok\n")

    monkeypatch.setattr(prepare_macmini_qa, "QA_ROOT", qa_root)
    monkeypatch.setattr(prepare_macmini_qa, "run", fake_run)

    target = prepare_macmini_qa._install_kubeconfig("foundry-qa")

    assert target == qa_root / "state" / "kubeconfig"
    assert target.stat().st_mode & 0o777 == 0o600
    assert commands[0][-1] == "colima-foundry-qa"
    assert commands[1][0:2] == ("kubectl", "--kubeconfig")
