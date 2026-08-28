"""Ontology property projection for an AI FDE business record."""

from __future__ import annotations

import re
from collections.abc import Mapping


def ontology_property(field: Mapping[str, object]) -> dict[str, object]:
    """Keep source fields read-only while allowing governed lifecycle edits."""

    api_name = str(field["apiName"])
    is_status = api_name == "status"
    return {
        "apiName": api_name,
        "displayName": field["displayName"],
        "column": _snake(api_name),
        "type": field["type"],
        "nullable": field.get("required") is not True,
        "indexed": is_status or api_name.endswith("Id"),
        "editable": is_status,
        "editPolicy": "edit_wins" if is_status else "source_wins",
    }


def _snake(value: str) -> str:
    separated = re.sub(r"(?<!^)(?=[A-Z])", "_", value).replace("-", "_").replace(" ", "_")
    return re.sub(r"_+", "_", separated).strip("_").lower()


__all__ = ["ontology_property"]
