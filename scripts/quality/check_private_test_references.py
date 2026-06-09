from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "private_test_references.json"
PRIVATE_CORE_CALL = re.compile(r"\bcore\._[A-Za-z0-9_]+\b")


def private_references() -> list[dict[str, object]]:
    references: list[dict[str, object]] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            matches = sorted(set(PRIVATE_CORE_CALL.findall(line)))
            for match in matches:
                references.append(
                    {
                        "file": str(path.relative_to(ROOT)),
                        "line": line_number,
                        "reference": match,
                    }
                )
    return references


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-count", type=int, default=17)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    references = private_references()
    report = {"max_count": args.max_count, "count": len(references), "references": references}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if len(references) > args.max_count:
        print(f"Private test references increased: {len(references)} > {args.max_count}")
        print(f"Report: {args.output.relative_to(ROOT)}")
        return 1
    print(f"Private test references {len(references)} <= {args.max_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
