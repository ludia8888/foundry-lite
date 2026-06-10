"""Self-test for the Foundry-lite CodeQL queries.

Per docs/quality-gate-roadmap.md §0.4, every gate ships with a self-test
that demonstrates the gate would actually fire on the hazard shape it
claims to prohibit.

The CodeQL queries take minutes to run, so this self-test focuses on
*structural validity* (the .ql files are parseable, the qlpack.yml is
well-formed, the run.sh script is invokable) rather than executing
the full analysis. The full analysis runs in CI and in the local
`pnpm ci:gate` step via scripts/quality/codeql/run.sh.

If codeql CLI is not on PATH (local dev without Homebrew), the structural
checks still run; only the optional full-DB build is skipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUERIES_DIR = REPO_ROOT / "scripts" / "quality" / "codeql" / "queries"
RUN_SCRIPT = REPO_ROOT / "scripts" / "quality" / "codeql" / "run.sh"

# This entire module needs the repo layout, so skip gracefully when invoked
# from somewhere else (mutmut copies the tree into mutants/ for instance).
# Uses an inline pytest.skip at import time — matches the pattern in
# test_quality_random_and_parallel.py, test_quality_semgrep_rules.py, and
# test_quality_service_wiring.py so check_no_test_bypasses allowlists it
# under a single self-test marker family.
if not QUERIES_DIR.exists():
    pytest.skip(
        "quality self-tests require the scripts/quality/codeql/queries tree",
        allow_module_level=True,
    )


def test_qlpack_is_present_and_declares_python_dependency() -> None:
    """qlpack.yml is the manifest CodeQL reads when running the directory."""
    qlpack = QUERIES_DIR / "qlpack.yml"
    assert qlpack.exists(), "qlpack.yml missing"
    text = qlpack.read_text()
    assert "codeql/python-all" in text
    assert "foundry-lite" in text


def test_every_query_cites_a_guideline_section() -> None:
    """Per docs/quality-gate-roadmap.md §0.6, every gate must name the
    guideline clause it enforces."""
    queries = sorted(QUERIES_DIR.glob("*.ql"))
    assert queries, "no .ql query files found"
    missing = [q.name for q in queries if "§" not in q.read_text()]
    assert not missing, f"queries missing §X.Y citation: {missing}"


def test_every_query_declares_id_and_severity() -> None:
    queries = sorted(QUERIES_DIR.glob("*.ql"))
    for query in queries:
        text = query.read_text()
        assert "@id foundry-lite/" in text, f"{query.name} missing @id"
        assert "@problem.severity" in text, f"{query.name} missing @problem.severity"
        assert "@kind" in text, f"{query.name} missing @kind"


def test_run_script_is_executable() -> None:
    assert RUN_SCRIPT.exists(), "run.sh missing"
    mode = RUN_SCRIPT.stat().st_mode
    assert mode & 0o111, "run.sh is not executable"


def test_run_script_skips_gracefully_when_codeql_missing() -> None:
    """The local gate must not block a developer who has not installed
    the heavyweight codeql CLI. The script prints WARN and exits 0."""
    if shutil.which("codeql"):
        pytest.skip("codeql is installed; the skip-when-missing path is not exercised here")
    result = subprocess.run(
        ["bash", str(RUN_SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "WARN" in result.stderr or "WARN" in result.stdout
