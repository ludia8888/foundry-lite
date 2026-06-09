from __future__ import annotations

import os

from foundry_lite_api.main import healthz
from foundry_lite_cli.main import main


def test_api_healthz_returns_ok() -> None:
    assert healthz() == {"status": "ok"}


def test_cli_demo_seed_smoke(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(tmp_path / "cli"))
    main(["demo", "seed"])
    assert os.path.exists("examples/supply-chain-demo/README.md")
