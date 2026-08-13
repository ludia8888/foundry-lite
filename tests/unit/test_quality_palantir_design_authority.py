"""Self-tests for the official Palantir design-authority gate."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.quality.check_palantir_design_authority import findings


def test_the_checked_in_design_decisions_use_official_palantir_sources() -> None:
    assert findings() == []


def test_an_adr_without_an_official_source_is_rejected(tmp_path: Path) -> None:
    _write_minimum_tree(tmp_path)
    (tmp_path / "docs" / "adr" / "0001-decision.md").write_text(
        "# Decision\n\n## Consequences\n\nproof\n\n## Non-goals\n\ncopying\n",
        encoding="utf-8",
    )

    assert any("missing ## Official" in issue for issue in findings(tmp_path))


def test_a_parity_matrix_cannot_use_a_non_official_source(tmp_path: Path) -> None:
    _write_minimum_tree(tmp_path, source="https://example.com/copied")

    assert any("non-official design source" in issue for issue in findings(tmp_path))


def _write_minimum_tree(tmp_path: Path, source: str | None = None) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    official = "https://www.palantir.com/docs/foundry/functions/api-object-sets"
    (adr_dir / "0001-decision.md").write_text(
        "# Decision\n\n## Official Palantir design sources\n\n"
        f"- {official}\n\n## Consequences\n\nproof\n\n## Non-goals\n\ncopying\n",
        encoding="utf-8",
    )
    payload = {"officialSources": [source or official]}
    (tmp_path / "docs" / "sample-parity-matrix.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
