from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_log_has_trace_keys as gate


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def test_log_trace_key_gate_flags_direct_logger_call_without_trace_key(tmp_path: Path) -> None:
    _write(
        tmp_path / "apps" / "api" / "handler.py",
        """
import logging

logger = logging.getLogger(__name__)

def handler() -> None:
    logger.info("object loaded")
""",
    )

    findings = gate.collect_findings((tmp_path,))

    assert [finding.code for finding in findings] == ["logger_call_missing_trace_key"]


def test_log_trace_key_gate_accepts_direct_logger_call_with_extra_trace_key(tmp_path: Path) -> None:
    _write(
        tmp_path / "apps" / "api" / "handler.py",
        """
import logging

logger = logging.getLogger(__name__)

def handler(request_id: str) -> None:
    logger.info("object loaded", extra={"request_id": request_id})
""",
    )

    assert gate.collect_findings((tmp_path,)) == []


def test_log_trace_key_gate_flags_log_event_without_trace_key(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "observability_user.py",
        """
import logging
from foundry_lite.observability.logging import log_event

def emit() -> None:
    log_event(logging.getLogger(__name__), "object_loaded")
""",
    )

    findings = gate.collect_findings((tmp_path,))

    assert [finding.code for finding in findings] == ["log_event_missing_trace_key"]


def test_log_trace_key_gate_accepts_log_event_with_trace_key(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "observability_user.py",
        """
import logging
from foundry_lite.observability.logging import log_event

def emit(tenant_id: str) -> None:
    log_event(logging.getLogger(__name__), "object_loaded", tenant_id=tenant_id)
""",
    )

    assert gate.collect_findings((tmp_path,)) == []


def test_log_trace_key_gate_writes_json_report(tmp_path: Path) -> None:
    _write(
        tmp_path / "bad.py",
        """
import logging

def emit() -> None:
    logging.warning("unlinked warning")
""",
    )
    output = tmp_path / "log_trace_keys.json"
    findings = gate.collect_findings((tmp_path,))

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"] == 0
    assert report["gate_pass"] is False
    assert report["violations"][0]["code"] == "logger_call_missing_trace_key"
