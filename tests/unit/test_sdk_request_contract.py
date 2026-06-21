from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_sdk_request_contract_covers_all_frontend_surface_routes() -> None:
    result = subprocess.run(
        ["node", "tests/sdk/request_contract.mjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_sdk_request_contract_covers_frontend_foundation_helpers() -> None:
    result = subprocess.run(
        ["node", "tests/sdk/request_contract.mjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
