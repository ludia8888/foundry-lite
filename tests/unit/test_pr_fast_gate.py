from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_pr_plan_classifies_docs_without_booting_product_lanes() -> None:
    gate = _load_module(ROOT / "scripts/quality/pr_fast_gate.py", "pr_fast_gate_docs")

    plan = gate.build_plan(("README.md", "docs/quality-gate-roadmap.md"))

    assert plan.is_docs_only is True
    assert plan.has_backend is False
    assert plan.has_frontend is False
    assert plan.selected_tests == ()


def test_pr_plan_selects_changed_and_directly_importing_tests(monkeypatch, tmp_path: Path) -> None:
    gate = _load_module(ROOT / "scripts/quality/pr_fast_gate.py", "pr_fast_gate_selection")
    source = tmp_path / "libs/foundry_lite/application/services/reservation_engine.py"
    source.parent.mkdir(parents=True)
    source.write_text("def reserve() -> None:\n    return None\n", encoding="utf-8")
    direct_test = tmp_path / "tests/unit/test_reservation_engine.py"
    direct_test.parent.mkdir(parents=True)
    direct_test.write_text(
        "from foundry_lite.application.services.reservation_engine import reserve\n",
        encoding="utf-8",
    )
    changed_test = tmp_path / "tests/unit/test_changed_contract.py"
    changed_test.write_text("def test_changed() -> None:\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    plan = gate.build_plan(
        (
            "libs/foundry_lite/application/services/reservation_engine.py",
            "tests/unit/test_changed_contract.py",
        )
    )

    assert plan.has_backend is True
    assert plan.source_files_without_tests == ()
    assert plan.selected_tests == (
        "tests/unit/test_changed_contract.py",
        "tests/unit/test_reservation_engine.py",
    )


def test_pr_plan_fails_closed_when_source_has_no_direct_or_changed_test(monkeypatch, tmp_path: Path) -> None:
    gate = _load_module(ROOT / "scripts/quality/pr_fast_gate.py", "pr_fast_gate_missing_test")
    source = tmp_path / "libs/foundry_lite/application/services/orphan_change.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    tests = tmp_path / "tests/unit"
    tests.mkdir(parents=True)
    (tests / "test_unrelated.py").write_text("def test_ok() -> None:\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    plan = gate.build_plan(("libs/foundry_lite/application/services/orphan_change.py",))

    assert plan.source_files_without_tests == ("libs/foundry_lite/application/services/orphan_change.py",)


def test_pr_diff_security_detects_high_confidence_secret_and_unsafe_python() -> None:
    gate = _load_module(ROOT / "scripts/quality/pr_fast_gate.py", "pr_fast_gate_security")
    fake_aws_key = "AKIA" + ("A" * 16)
    dynamic_execution = "e" + "val(user_input)"

    violations = gate.security_violations(
        {
            "libs/example.py": [dynamic_execution],
            "config/runtime.env": [f"ACCESS_KEY={fake_aws_key}"],
        }
    )

    assert any(item.endswith(":dynamic-eval") for item in violations)
    assert any(item.endswith(":aws-access-key") for item in violations)


def test_pr_diff_security_allows_fixed_subprocess_and_test_fixtures() -> None:
    gate = _load_module(ROOT / "scripts/quality/pr_fast_gate.py", "pr_fast_gate_security_allowed")

    violations = gate.security_violations(
        {
            "libs/example.py": ["subprocess.run(['git', 'status'], check=True)"],
            "tests/unit/test_policy.py": ["eval('1 + 1')"],
        }
    )

    assert violations == []


def test_pr_security_report_has_quantitative_gate_contract(monkeypatch, tmp_path: Path) -> None:
    gate = _load_module(ROOT / "scripts/quality/pr_fast_gate.py", "pr_fast_gate_report")
    report_path = tmp_path / "pr_diff_security.json"
    monkeypatch.setattr(gate, "SECURITY_REPORT_PATH", report_path)
    monkeypatch.setattr(gate, "_added_lines_by_file", lambda _base, _head: {})

    result = gate.run_security("base", "head")

    assert result == 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "count": 0,
        "violations": [],
        "baseline": 0,
        "gate_pass": True,
    }


def test_pr_static_profile_excludes_network_and_product_rehearsals() -> None:
    static = _load_module(ROOT / "scripts/quality/run_static_checks.py", "pr_static_profile")

    names = {name for name, _command in static._all_checks("pr")}

    assert {"pyright", "mypy", "bandit", "ruff-lint", "ruff-format", "tach"} <= names
    assert "semgrep" not in names
    assert "pip-audit" not in names
    assert "gitleaks" not in names
    assert "pipeline-artifact-execution" not in names
    assert "frontend-foundation" not in names
