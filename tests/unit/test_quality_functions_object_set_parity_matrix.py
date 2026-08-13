"""Self-tests for the official-source Functions/ObjectSet parity gate."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.quality.check_functions_object_set_parity_matrix import MATRIX, ROOT, findings


def test_the_checked_in_functions_object_set_matrix_is_valid() -> None:
    assert findings() == []


def test_current_cannot_hide_a_gap(tmp_path: Path) -> None:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    capability = next(item for item in payload["capabilities"] if item["status"] == "current")
    capability["gaps"] = ["hidden gap"]
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("current status requires" in issue for issue in findings(ROOT, path))


def test_non_official_source_domain_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    payload["officialSources"][0]["url"] = "https://example.com/copied-doc"
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("official Palantir HTTPS URL" in issue for issue in findings(ROOT, path))
