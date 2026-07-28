"""Isolated subprocess entrypoint for bounded PDF layout extraction."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from foundry_lite.infrastructure.adapters.pdf_layout_processor import _pypdf_layout_extract
    from foundry_lite.infrastructure.adapters.pdf_page_selection import PdfPageSelection
    from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfDocumentError

    try:
        payload = json.load(sys.stdin)
        selection_payload = payload["selection"]
        selection = PdfPageSelection(
            start=int(selection_payload["start"]),
            limit=int(selection_payload["limit"]) if selection_payload["limit"] is not None else None,
        )
        fragments = _pypdf_layout_extract(
            str(payload["sourcePath"]),
            int(payload["maxPages"]),
            selection,
        )
        result: dict[str, object] = {
            "kind": "ok",
            "fragments": [asdict(fragment) for fragment in fragments],
        }
    except PdfDocumentError as exc:
        result = {"kind": "document_error", "reason": exc.reason, "page": exc.page}
    except Exception:
        result = {"kind": "document_error", "reason": "corrupt_pdf", "page": None}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
