from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = ROOT / "libs" / "foundry_lite" / "application"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "application_module_size.json"


def python_modules() -> list[Path]:
    return sorted(path for path in APPLICATION_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-lines", type=int, default=500)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    modules = [
        {
            "file": str(path.relative_to(ROOT)),
            "lines": line_count(path),
        }
        for path in python_modules()
    ]
    violations = [module for module in modules if module["lines"] > args.max_lines]
    report = {"max_lines": args.max_lines, "modules": modules, "violations": violations}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if violations:
        print(f"Application module size violations, max {args.max_lines} lines:")
        for violation in violations:
            print(f"- {violation['file']}: {violation['lines']} lines")
        print(f"Report: {args.output.relative_to(ROOT)}")
        return 1
    print(f"Application module sizes OK: all modules <= {args.max_lines} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
