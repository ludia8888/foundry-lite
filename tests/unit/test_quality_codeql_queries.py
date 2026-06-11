"""Self-test for the Foundry-lite CodeQL queries.

Per docs/quality-gate-roadmap.md §0.4, every gate ships with a self-test
that demonstrates the gate would actually fire on the hazard shape it
claims to prohibit.

The CodeQL queries take minutes to run, so this self-test focuses on
*structural validity* (the .ql files are parseable, the qlpack.yml is
well-formed, the run.sh script is invokable) rather than executing
the full analysis. The full analysis runs in GitHub Actions; run.sh is
a local debugging helper only.

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


def test_api_graph_queries_use_supported_instance_api() -> None:
    """Regression shield for the P7 failure on 2026-06-10.

    CodeQL Python API::Node exposes getAnInstance(), not getInstance(). The
    latter compiles nowhere and breaks the GitHub-hosted data-flow gate before
    it can produce findings.
    """

    offenders = [query.name for query in QUERIES_DIR.glob("*.ql") if ".getInstance()" in query.read_text()]
    assert offenders == []


def test_dataflow_queries_use_supported_argument_api() -> None:
    """Regression shield for the P7 failure on 2026-06-11.

    CodeQL Python DataFlow::CallCfgNode exposes getArg(i). It does not expose
    getAnArg(), so any-argument matching must quantify over getArg(i).
    """

    offenders = [query.name for query in QUERIES_DIR.glob("*.ql") if ".getAnArg()" in query.read_text()]
    assert offenders == []


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
        env={**os.environ, "PATH": "/usr/bin:/bin", "CI": "", "FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS": "0"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "WARN" in result.stderr or "WARN" in result.stdout


def test_run_script_blocks_missing_codeql_in_strict_mode() -> None:
    if shutil.which("codeql"):
        pytest.skip("codeql is installed; the strict missing-tool path is not exercised here")
    result = subprocess.run(
        ["bash", str(RUN_SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "/usr/bin:/bin", "CI": "", "FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS": "1"},
        check=False,
    )
    assert result.returncode == 1
    assert "cannot skip the P7 data-flow gate" in result.stderr
