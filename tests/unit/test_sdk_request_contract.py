from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _node_executable() -> str:
    node = shutil.which("node")
    if node is not None:
        return node
    for candidate in (Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")):
        if candidate.exists():
            return str(candidate)
    raise AssertionError("Node.js executable is required for tests/sdk/request_contract.mjs")


def _run_request_contract() -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        # The proof imports the generated TypeScript source directly, so Node has to
        # strip its types rather than refuse an unknown ".ts" extension.
        [
            _node_executable(),
            "--disable-warning=ExperimentalWarning",
            "--experimental-strip-types",
            "tests/sdk/request_contract.mjs",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result


def test_sdk_request_contract_covers_all_frontend_surface_routes() -> None:
    result = _run_request_contract()

    assert result.returncode == 0, result.stdout + result.stderr


def test_sdk_request_contract_covers_frontend_foundation_helpers() -> None:
    result = _run_request_contract()

    assert result.returncode == 0, result.stdout + result.stderr
