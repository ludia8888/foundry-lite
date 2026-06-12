from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.quality import check_regression_test_per_bugfix as gate


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _write(tmp_path, "libs/example.py", "VALUE = 1\n")
    base = _commit(tmp_path, "Initial commit")
    return tmp_path, base


def test_regression_gate_flags_bugfix_commit_without_tests(tmp_path: Path) -> None:
    repo, base = _init_repo(tmp_path)
    _write(repo, "libs/example.py", "VALUE = 2\n")
    sha = _commit(repo, "Fix value mismatch")

    findings = gate.collect_findings(repo, base_ref=base)

    assert findings == [
        gate.RegressionTestFinding(
            code="bugfix_commit_missing_regression_test",
            commit=sha,
            subject="Fix value mismatch",
            changed_paths=["libs/example.py"],
            message="Bug-fix/patch commit must include a regression test change under tests/",
        )
    ]


def test_regression_gate_accepts_bugfix_commit_with_tests(tmp_path: Path) -> None:
    repo, base = _init_repo(tmp_path)
    _write(repo, "libs/example.py", "VALUE = 2\n")
    _write(repo, "tests/test_example.py", "def test_value():\n    assert True\n")
    _commit(repo, "Patch value mismatch")

    assert gate.collect_findings(repo, base_ref=base) == []


def test_regression_gate_ignores_non_bugfix_commit(tmp_path: Path) -> None:
    repo, base = _init_repo(tmp_path)
    _write(repo, "libs/example.py", "VALUE = 2\n")
    _commit(repo, "Add value constant")

    assert gate.collect_findings(repo, base_ref=base) == []


def test_regression_gate_writes_json_report(tmp_path: Path) -> None:
    repo, base = _init_repo(tmp_path)
    output = tmp_path / "artifacts" / "quality" / "regression_test_per_bugfix.json"
    _write(repo, "libs/example.py", "VALUE = 2\n")
    _commit(repo, "Bugfix value mismatch")
    findings = gate.collect_findings(repo, base_ref=base)

    gate.write_report(output, findings, root=repo, base_ref=base)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"bugfix_commit_missing_regression_test"' in report
