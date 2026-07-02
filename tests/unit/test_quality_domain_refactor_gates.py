from __future__ import annotations

import json
from pathlib import Path

from scripts.quality.check_domain_rule_tests import collect_findings as domain_rule_findings
from scripts.quality.check_no_service_mixin_growth import collect_findings as mixin_findings

ROOT = Path(__file__).resolve().parents[2]


def test_no_service_mixin_growth_gate_is_registered_and_current_repo_passes() -> None:
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "quality:no-service-mixin-growth" in package_json["scripts"]
    assert mixin_findings() == []


def test_domain_rule_tests_gate_is_registered_and_current_repo_passes() -> None:
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "quality:domain-rule-tests" in package_json["scripts"]
    assert domain_rule_findings() == []


def test_no_service_mixin_growth_gate_fails_new_mixin(tmp_path: Path) -> None:
    source = tmp_path / "services"
    source.mkdir()
    (source / "bad.py").write_text("class NewDebtMixin:\n    pass\n", encoding="utf-8")

    findings = mixin_findings((source,))

    assert [finding.code for finding in findings] == ["new_service_mixin_debt"]


def test_domain_rule_tests_gate_fails_untested_rule(tmp_path: Path) -> None:
    domain = tmp_path / "libs" / "foundry_lite" / "domain"
    tests = tmp_path / "tests"
    domain.mkdir(parents=True)
    tests.mkdir()
    (domain / "rules.py").write_text("def decide() -> str:\n    return 'ok'\n", encoding="utf-8")
    (tests / "test_other.py").write_text("def test_other() -> None:\n    assert True\n", encoding="utf-8")

    findings = domain_rule_findings(domain, tests)

    assert [finding.code for finding in findings] == ["missing_domain_rule_test"]
