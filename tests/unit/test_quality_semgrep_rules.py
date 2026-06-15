"""Self-test for Foundry-lite Semgrep rules.

Per docs/quality-gate-roadmap.md §0.4, every gate must ship with a self-test
that confirms it would actually fail on the code shape it claims to prohibit.
This module synthesises minimal violation fixtures in a tmp directory, runs
semgrep against them, and asserts each rule fires exactly once.

Negative self-tests (clean fixtures that must NOT trigger a rule) live in the
parametrised "clean" cases. A rule that incorrectly fires on clean code is a
false-positive regression.
"""

from __future__ import annotations

import os as _os

if not _os.path.exists(_os.path.join(_os.path.dirname(__file__), "..", "..", "scripts", "quality")):
    import pytest

    pytest.skip(
        "quality self-tests require the scripts/ tree; skipped under mutmut's mutants/ copy.",
        allow_module_level=True,
    )

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_FILE = REPO_ROOT / "scripts" / "quality" / "semgrep-rules" / "foundry-lite.yml"


@pytest.fixture
def semgrep_available() -> None:
    if shutil.which("semgrep") is None:
        # The semgrep binary is installed as a dev dependency via uv. If a
        # CI environment skips dev deps the gate is meaningless, so we fail
        # loudly rather than silently skipping.
        pytest.fail("semgrep CLI not on PATH; install via `uv sync` or `uv add --dev semgrep`")


def _semgrep_env() -> dict[str, str]:
    import certifi

    env = _os.environ.copy()
    env.setdefault("SSL_CERT_FILE", certifi.where())
    env.setdefault("SEMGREP_SEND_METRICS", "off")
    env.setdefault("SEMGREP_ENABLE_VERSION_CHECK", "0")
    return env


def _run_semgrep(tmp_path: Path) -> dict:
    result = subprocess.run(
        [
            "semgrep",
            "--config",
            str(RULES_FILE),
            "--metrics",
            "off",
            "--quiet",
            "--json",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_semgrep_env(),
    )
    if result.returncode not in (0, 1):
        # semgrep returns 1 when findings exist; anything else is a tool error.
        raise AssertionError(f"semgrep failed: rc={result.returncode}, stderr={result.stderr}")
    return json.loads(result.stdout)


def _findings_by_rule(report: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in report.get("results", []):
        rule = finding["check_id"].rsplit(".", 1)[-1]
        counts[rule] = counts.get(rule, 0) + 1
    return counts


# ──────────────────────────────────────────────────────────────────────────
# Positive self-tests: each fixture violates exactly one rule.
# ──────────────────────────────────────────────────────────────────────────


def test_router_no_direct_transaction_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "apps" / "api" / "router.py"
    target.parent.mkdir(parents=True)
    target.write_text("def handler(foundry):\n    with foundry.engine.begin() as conn:\n        return conn\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-router-no-direct-transaction") == 1


def test_router_no_repository_access_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "apps" / "api" / "router.py"
    target.parent.mkdir(parents=True)
    target.write_text("def handler(foundry):\n    return foundry.action_repository\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-router-no-repository-access") == 1


def test_repository_no_domain_errors_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "libs" / "foundry_lite" / "infrastructure" / "repositories" / "fake.py"
    target.parent.mkdir(parents=True)
    target.write_text("class ValidationFailed(Exception):\n    pass\ndef write():\n    raise ValidationFailed('bad')\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-repository-no-domain-errors") == 1


def test_action_service_no_bare_audit_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "libs" / "foundry_lite" / "application" / "services" / "action_service.py"
    target.parent.mkdir(parents=True)
    target.write_text("class A:\n    def m(self):\n        self._audit('x')\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-action-service-no-bare-audit") == 1


def test_no_bare_except_pass_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("def m():\n    try:\n        pass\n    except Exception:\n        pass\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-no-bare-except-pass") == 1


def test_no_eval_exec_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("def m(s):\n    return eval(s)\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-no-eval-exec") == 1


def test_no_fstring_sql_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "libs" / "foundry_lite" / "db.py"
    target.parent.mkdir(parents=True)
    target.write_text("def m(conn, table):\n    return conn.execute(f'select * from {table}')\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-no-fstring-sql") == 1


def test_application_no_vendor_sdk_fires(semgrep_available, tmp_path: Path) -> None:
    target = tmp_path / "libs" / "foundry_lite" / "application" / "leak.py"
    target.parent.mkdir(parents=True)
    target.write_text("import sqlalchemy\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts.get("foundry-lite-application-no-vendor-sdk") == 1


# ──────────────────────────────────────────────────────────────────────────
# Negative self-test: clean tree triggers no rule.
# ──────────────────────────────────────────────────────────────────────────


def test_clean_tree_triggers_no_rule(semgrep_available, tmp_path: Path) -> None:
    code = tmp_path / "libs" / "foundry_lite" / "application" / "ok.py"
    code.parent.mkdir(parents=True)
    code.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    counts = _findings_by_rule(_run_semgrep(tmp_path))
    assert counts == {}
