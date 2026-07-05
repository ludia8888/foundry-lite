from __future__ import annotations

import json

from scripts.quality import check_core_service_wiring


def test_core_service_wiring_report_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "core_service_wiring.json"

    assert check_core_service_wiring.main(["--output", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["declared_missing_from_map"] == []
    assert report["map_keys_not_declared"] == []
    assert report["enforced_unused_map_keys"] == []
    assert report["forbidden_edges"] == []
