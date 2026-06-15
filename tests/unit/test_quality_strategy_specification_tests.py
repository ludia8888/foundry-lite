from __future__ import annotations

from pathlib import Path

from scripts.quality import check_strategy_specification_tests as gate


def _write_python(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_strategy_specification_gate_flags_missing_direct_test(tmp_path: Path) -> None:
    source_dir = tmp_path / "libs" / "foundry_lite" / "application"
    test_dir = tmp_path / "tests"
    source = _write_python(
        tmp_path,
        "libs/foundry_lite/application/query_filters.py",
        "def matches_filter(properties, filter_ast):\n    return True\n",
    )
    _write_python(
        tmp_path,
        "tests/test_core_only.py",
        "from foundry_lite.application.foundry import FoundryLite\n\n"
        "def test_query_through_core():\n    assert FoundryLite\n",
    )

    findings = gate.collect_findings(source_dir=source_dir, test_dirs=(test_dir,), root=tmp_path)

    assert findings == [
        gate.StrategySpecFinding(
            code="strategy_specification_missing_direct_test",
            path=str(source.relative_to(tmp_path)),
            module="foundry_lite.application.query_filters",
            symbols=["matches_filter"],
            message="Strategy/Specification rule module has no direct unit/property test import",
        )
    ]


def test_strategy_specification_gate_accepts_direct_module_import(tmp_path: Path) -> None:
    source_dir = tmp_path / "libs" / "foundry_lite" / "application"
    test_dir = tmp_path / "tests"
    _write_python(
        tmp_path,
        "libs/foundry_lite/application/preconditions.py",
        "class AmountSpecification:\n    pass\n",
    )
    _write_python(
        tmp_path,
        "tests/test_preconditions.py",
        "from foundry_lite.application.preconditions import AmountSpecification\n\n"
        "def test_amount_specification_is_directly_testable():\n    assert AmountSpecification\n",
    )

    assert gate.collect_findings(source_dir=source_dir, test_dirs=(test_dir,), root=tmp_path) == []


def test_strategy_specification_gate_accepts_parent_package_symbol_import(tmp_path: Path) -> None:
    source_dir = tmp_path / "libs" / "foundry_lite" / "application"
    test_dir = tmp_path / "tests"
    _write_python(
        tmp_path,
        "libs/foundry_lite/application/query_filters.py",
        "FILTER_OPERATIONS = {'eq': lambda current, value: current == value}\n",
    )
    _write_python(
        tmp_path,
        "tests/test_query_filters.py",
        "from foundry_lite.application import FILTER_OPERATIONS\n\n"
        "def test_filter_operations():\n    assert FILTER_OPERATIONS\n",
    )

    assert gate.collect_findings(source_dir=source_dir, test_dirs=(test_dir,), root=tmp_path) == []


def test_strategy_specification_gate_ignores_ordinary_helpers(tmp_path: Path) -> None:
    source_dir = tmp_path / "libs" / "foundry_lite" / "application"
    test_dir = tmp_path / "tests"
    _write_python(
        tmp_path,
        "libs/foundry_lite/application/helpers.py",
        "def normalize_name(value):\n    return value.strip()\n",
    )

    assert gate.collect_findings(source_dir=source_dir, test_dirs=(test_dir,), root=tmp_path) == []


def test_strategy_specification_gate_writes_json_report(tmp_path: Path) -> None:
    source_dir = tmp_path / "libs" / "foundry_lite" / "application"
    test_dir = tmp_path / "tests"
    output = tmp_path / "artifacts" / "quality" / "strategy_specification_tests.json"
    _write_python(
        tmp_path,
        "libs/foundry_lite/application/safe_expression.py",
        "def evaluate_safe_expression(expression, properties):\n    return True\n",
    )
    findings = gate.collect_findings(source_dir=source_dir, test_dirs=(test_dir,), root=tmp_path)

    gate.write_report(output, findings, source_dir=source_dir, test_dirs=(test_dir,), root=tmp_path)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"strategy_specification_missing_direct_test"' in report
