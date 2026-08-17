from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.operations import macmini_qa_guard
from scripts.operations.macmini_qa_guard import (
    ALLOWED_NAMESPACES,
    COLIMA_PROFILE,
    assert_namespace,
    assert_profile,
)


def test_macmini_qa_guard_allows_only_dedicated_namespaces() -> None:
    assert ALLOWED_NAMESPACES == {"foundry-qa", "foundry-qa-recovery"}
    for namespace in ALLOWED_NAMESPACES:
        assert_namespace(namespace)
    with pytest.raises(ValueError, match="namespace_not_allowed"):
        assert_namespace("default")


def test_macmini_qa_guard_allows_only_dedicated_colima_profile() -> None:
    assert_profile(COLIMA_PROFILE)
    with pytest.raises(ValueError, match="profile_not_allowed"):
        assert_profile("default")


def test_macmini_qa_run_prepends_private_tools_for_noninteractive_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qa_root = tmp_path / "foundry-qa"
    observed: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(macmini_qa_guard, "QA_ROOT", qa_root)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(macmini_qa_guard.subprocess, "run", fake_run)

    result = macmini_qa_guard.run(("colima", "status", "foundry-qa"), timeout_seconds=30)

    assert result.return_code == 0
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["PATH"].split(":") == [str(qa_root / "bin"), "/opt/homebrew/bin", "/usr/bin", "/bin"]
    assert observed["argv"] == ["colima", "status", "foundry-qa"]
