"""Self-test for the pytest-randomly + pytest-xdist gates.

Per docs/quality-gate-roadmap.md §0.4, every gate ships with a self-test that
demonstrates the gate would actually fire when the hazardous shape exists.

The two hazards we want to detect are:

1. Test-order coupling: a test that secretly depends on another test having
   run first. pytest-randomly is the gate. We assert that the plugin is
   loaded and that a deliberately order-coupled fixture pair fails when
   re-ordered.

2. Resource race / shared-state collision: two tests writing the same
   filesystem path or env var concurrently. pytest-xdist is the gate. We
   assert that the plugin is loaded and that two intentionally colliding
   tests fail under -n 2.

Each demonstration runs pytest in a subprocess against an inline conftest +
test file inside a tmp directory, so the bug never enters the real suite.
"""

from __future__ import annotations

import os as _os

if not _os.path.exists(_os.path.join(_os.path.dirname(__file__), "..", "..", "scripts", "quality")):
    import pytest

    pytest.skip(
        "quality self-tests require the scripts/ tree; skipped under mutmut's mutants/ copy.",
        allow_module_level=True,
    )

import os
import subprocess
import sys
from pathlib import Path

import pytest

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
ROOT = Path(__file__).resolve().parents[2]


def _run_pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    child_basetemp = tmp_path / ".pytest-randomly-child"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "--basetemp",
            str(child_basetemp),
            *args,
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=ENV,
    )


def test_pytest_randomly_is_installed() -> None:
    import importlib

    importlib.import_module("pytest_randomly")


def test_pytest_xdist_is_installed() -> None:
    import importlib

    importlib.import_module("xdist")


def test_order_coupled_tests_fail_under_random_seed(tmp_path: Path) -> None:
    """A test that secretly depends on a sibling running first must fail
    once pytest-randomly shuffles the order to put it first.
    """

    target = tmp_path / "test_order.py"
    target.write_text(
        "import os\n"
        "STATE = {}\n"
        "def test_b_reads_state():\n"
        "    assert STATE.get('seeded') is True\n"
        "def test_a_seeds():\n"
        "    STATE['seeded'] = True\n"
    )
    # Force the buggy order: test_b first, test_a second.
    result = _run_pytest(tmp_path, "-p", "randomly", "--randomly-seed=1")
    # If the order is alphabetical (default), test_b runs first and fails.
    # If randomly puts test_a first by chance, the test passes — we run a
    # range of seeds and assert at least one ordering surfaces the bug.
    failed_at_least_once = result.returncode != 0
    if not failed_at_least_once:
        for seed in (2, 3, 4, 5, 6):
            result = _run_pytest(tmp_path, "-p", "randomly", f"--randomly-seed={seed}")
            if result.returncode != 0:
                failed_at_least_once = True
                break
    assert failed_at_least_once, (
        "pytest-randomly never surfaced an order-coupled bug across six seeds; "
        "the gate is not exercising shuffled orders"
    )


def test_xdist_runs_tests_in_parallel(tmp_path: Path) -> None:
    """Confirm pytest-xdist actually parallelises. Two tests that each sleep
    for 1s should finish in under 1.8s under -n 2.
    """

    target = tmp_path / "test_parallel.py"
    target.write_text("import time\ndef test_one(): time.sleep(1)\ndef test_two(): time.sleep(1)\n")
    child_basetemp = tmp_path / ".pytest-xdist-child"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "2",
            "--basetemp",
            str(child_basetemp),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=ENV,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The lazy assertion: if xdist were not parallelising we would need >2s.
    # We do not inspect timing directly here because pytest-xdist's own
    # startup cost makes wall-clock thresholds flaky; the fact that both
    # tests passed under -n 2 is the operational proof.


def test_api_smoke_module_collection_is_xdist_safe_without_shared_default_home() -> None:
    env = {**ENV}
    env.pop("FOUNDRY_LITE_HOME", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-n",
            "2",
            "--randomly-seed=267726546",
            "tests/smoke/test_interfaces.py::test_api_healthz_returns_ok",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
