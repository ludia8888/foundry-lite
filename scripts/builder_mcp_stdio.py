#!/usr/bin/env python3
"""Local stdio entrypoint for the governed branch-first Builder MCP plane."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ontology_mcp_stdio import main as proxy_main


def main(argv: Sequence[str] | None = None) -> int:
    """Run the shared MCP proxy with the non-production Builder plane selected."""
    return proxy_main(("--plane", "builder", *(argv or sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
