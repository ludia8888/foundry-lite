"""Generate the TypeScript, browser, and Python SDKs from the active demo Ontology.

Thin CLI entrypoint; rendering lives in the scripts/sdk_generator package
(ontology parsing, client surface, TypeScript/browser templates). This path
stays stable because gates, package.json, and docs reference it directly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    # `python scripts/generate_sdk_ts.py` puts scripts/ (not the repo root) on
    # sys.path, so the scripts.sdk_generator package needs the root added.
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.sdk_generator.ontology import load_ontology  # noqa: E402
from scripts.sdk_generator.paths import (  # noqa: E402
    DEFAULT_ONTOLOGY,
    DEFAULT_PYTHON_INIT_OUTPUT,
    DEFAULT_PYTHON_OUTPUT,
    DEFAULT_TS_OUTPUT,
    _display_path,
)
from scripts.sdk_generator.python import render_python, render_python_init  # noqa: E402
from scripts.sdk_generator.surface import (  # noqa: E402,F401  (re-exported test surface)
    client_surface,
    render_client_surface_json,
)
from scripts.sdk_generator.typescript import render_typescript  # noqa: E402


def write_or_check_outputs(
    *,
    ontology_path: Path,
    ts_output: Path,
    python_output: Path | None = None,
    python_init_output: Path | None = None,
    should_check: bool,
) -> int:
    ontology = load_ontology(ontology_path)
    outputs = {
        ts_output: render_typescript(ontology),
    }
    if python_output is not None:
        outputs[python_output] = render_python(ontology)
    if python_init_output is not None:
        outputs[python_init_output] = render_python_init(ontology)
    if should_check:
        stale = [
            path
            for path, content in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("SDK generation check failed: generated outputs are stale or ontology apiNames changed.")
            for path in stale:
                print(f"- {_display_path(path)}")
            return 1
        print("SDK generation check passed: generated outputs match the active ontology.")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {_display_path(path)}")
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Foundry-lite TypeScript SDK from ontology YAML.")
    parser.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY)
    parser.add_argument("--ts-output", type=Path, default=DEFAULT_TS_OUTPUT)
    parser.add_argument("--python-output", type=Path, default=DEFAULT_PYTHON_OUTPUT)
    parser.add_argument("--python-init-output", type=Path, default=DEFAULT_PYTHON_INIT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return write_or_check_outputs(
        ontology_path=args.ontology,
        ts_output=args.ts_output,
        python_output=args.python_output,
        python_init_output=args.python_init_output,
        should_check=args.check,
    )


if __name__ == "__main__":
    sys.exit(main())
