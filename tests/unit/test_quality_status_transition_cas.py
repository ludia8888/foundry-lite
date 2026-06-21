from __future__ import annotations

from pathlib import Path

from scripts.quality import check_status_transition_cas as gate


def test_status_transition_cas_gate_flags_raw_status_update(tmp_path: Path) -> None:
    module = tmp_path / "raw_status_update.py"
    module.write_text(
        """
from sqlalchemy import update


def mark_done(transaction, table):
    transaction.execute(
        update(table)
        .where(table.c.tenant_id == "tenant-demo")
        .values(status="done")
    )
""".strip()
        + "\n",
        encoding="utf-8",
    )

    findings = gate.collect_findings((module,))

    assert len(findings) == 1
    assert findings[0].path.endswith("raw_status_update.py")
    assert "must use status_cas.py" in findings[0].message


def test_status_transition_cas_gate_allows_insert_initial_status(tmp_path: Path) -> None:
    module = tmp_path / "insert_initial_status.py"
    module.write_text(
        """
from sqlalchemy import insert


def insert_run(transaction, table):
    transaction.execute(insert(table).values(id="run-1", tenant_id="tenant-demo", status="running"))
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert gate.collect_findings((module,)) == []


def test_status_transition_cas_gate_allows_shared_cas_helper() -> None:
    status_cas = gate.ROOT / "libs" / "foundry_lite" / "infrastructure" / "repositories" / "status_cas.py"

    assert gate.collect_findings((status_cas,)) == []
