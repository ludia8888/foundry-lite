"""JSON-file contract for one isolated trained-model image."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast


class TrainedModelRequest(TypedDict):
    schemaVersion: int
    modelRef: str
    rows: list[dict[str, object]]


def main() -> int:
    request_path, result_path = map(Path, sys.argv[1:3])
    try:
        request = _read_request(request_path)
        rows = [_score_transaction(row) for row in request["rows"]]
        result = {
            "schemaVersion": 1,
            "status": "succeeded",
            "rows": rows,
            "runtimeEvidence": _runtime_evidence(len(rows)),
        }
    except Exception as exc:
        result = {
            "schemaVersion": 1,
            "status": "failed",
            "failure": {
                "type": "model_execution_error",
                "exceptionType": type(exc).__name__,
                "messageSha256": hashlib.sha256(str(exc).encode()).hexdigest(),
            },
        }
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return 0 if result["status"] == "succeeded" else 2


def _read_request(path: Path) -> TrainedModelRequest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("invalid trained-model request contract")
    if payload.get("modelRef") != "demo.transaction-risk":
        raise ValueError("modelRef is not served by this image")
    rows = payload.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("trained-model rows must be objects")
    return cast(TrainedModelRequest, payload)


def _score_transaction(row: Mapping[str, object]) -> dict[str, object]:
    amount = row.get("amount")
    if not isinstance(amount, int | float) or isinstance(amount, bool):
        raise ValueError("amount must be numeric")
    country = str(row.get("country") or "").upper()
    amount_score = min(float(amount) / 20_000.0, 0.8)
    country_score = 0.15 if country in {"IR", "KP", "SY"} else 0.0
    media_score = 0.05 if isinstance(row.get("document"), Mapping) else 0.0
    score = round(min(amount_score + country_score + media_score, 0.99), 4)
    return {"riskScore": score, "decision": "review" if score >= 0.7 else "allow"}


def _runtime_evidence(row_count: int) -> dict[str, object]:
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "environmentKeys": sorted(os.environ),
        "networkBlocked": _network_blocked(),
        "rootWriteBlocked": _root_write_blocked(),
        "outputDirectoryWriteBlocked": _output_directory_write_blocked(),
        "effectiveCapabilities": _status_value("CapEff"),
        "noNewPrivileges": _status_value("NoNewPrivs"),
        "inputRowCount": row_count,
        "outputRowCount": row_count,
        "warmPoolEnabled": False,
    }


def _network_blocked() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=0.1):
            return False
    except OSError:
        return True


def _root_write_blocked() -> bool:
    try:
        Path("/trained-model-root-write").write_text("unsafe", encoding="utf-8")
        return False
    except OSError:
        return True


def _output_directory_write_blocked() -> bool:
    try:
        Path("/model-output/unbounded-host-write").write_text("unsafe", encoding="utf-8")
        return False
    except OSError:
        return True


def _status_value(name: str) -> str:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}:"):
            return line.split(":", 1)[1].strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
