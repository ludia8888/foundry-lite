"""Require official Palantir design sources for major ADRs and parity registries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_PREFIX = "https://www.palantir.com/docs/foundry/"
ADR_SOURCE_HEADING = "## Official Palantir design sources"


def findings(root: Path = ROOT) -> list[str]:
    issues: list[str] = []
    adrs = sorted((root / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    matrices = sorted((root / "docs").glob("*parity-matrix.json"))
    if not adrs:
        issues.append("no numbered architecture decisions were found")
    if not matrices:
        issues.append("no public-behavior parity matrices were found")
    for path in adrs:
        issues.extend(_adr_findings(path, root))
    for path in matrices:
        issues.extend(_matrix_findings(path, root))
    return issues


def _adr_findings(path: Path, root: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(root)
    issues: list[str] = []
    if ADR_SOURCE_HEADING not in text:
        issues.append(f"{relative} is missing {ADR_SOURCE_HEADING}")
    if OFFICIAL_PREFIX not in text:
        issues.append(f"{relative} has no exact official Palantir Foundry source")
    if "## Consequences" not in text or "## Non-goals" not in text:
        issues.append(f"{relative} must state consequences and non-goals")
    return issues


def _matrix_findings(path: Path, root: Path) -> list[str]:
    relative = path.relative_to(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"{relative} is not readable JSON: {type(exc).__name__}"]
    if not isinstance(payload, Mapping):
        return [f"{relative} must contain one JSON object"]
    urls = _source_urls(payload.get("officialSources"))
    if not urls:
        return [f"{relative} has no officialSources URLs"]
    return [f"{relative} contains non-official design source {url}" for url in urls if not _is_official(url)]


def _source_urls(value: object) -> list[str]:
    if isinstance(value, Mapping):
        return [item for item in value.values() if isinstance(item, str)]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        urls: list[str] = []
        for item in value:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("url"), str):
                urls.append(str(item["url"]))
        return urls
    return []


def _is_official(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc in {"palantir.com", "www.palantir.com"}
        and url.startswith(OFFICIAL_PREFIX)
    )


def main() -> int:
    issues = findings()
    if issues:
        for issue in issues:
            print(f"Palantir design authority: {issue}")
        return 1
    print("Palantir design authority passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
