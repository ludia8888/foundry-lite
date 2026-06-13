from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_tenant_write_guard as gate


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def test_tenant_write_guard_flags_insert_without_tenant_id(tmp_path: Path) -> None:
    _write(
        tmp_path / "repo.py",
        """
from sqlalchemy import insert


def write(transaction, db):
    transaction.execute(insert(db.orders).values(id="order-1"))
""",
    )

    findings = gate.collect_findings((tmp_path,), tenant_tables={"orders"})

    assert [(finding.operation, finding.table) for finding in findings] == [("insert", "orders")]


def test_tenant_write_guard_flags_update_and_delete_without_tenant_where(tmp_path: Path) -> None:
    _write(
        tmp_path / "repo.py",
        """
from sqlalchemy import delete, update


def write(transaction, db):
    transaction.execute(update(db.orders).where(db.orders.c.id == "order-1").values(status="done"))
    transaction.execute(delete(db.orders).where(db.orders.c.id == "order-2"))
""",
    )

    findings = gate.collect_findings((tmp_path,), tenant_tables={"orders"})

    assert [(finding.operation, finding.table) for finding in findings] == [
        ("update", "orders"),
        ("delete", "orders"),
    ]


def test_tenant_write_guard_accepts_tenant_scoped_writes(tmp_path: Path) -> None:
    _write(
        tmp_path / "repo.py",
        """
from sqlalchemy import and_, delete, insert, update


def write(transaction, db, tenant_id):
    transaction.execute(insert(db.orders).values(id="order-1", tenant_id=tenant_id))
    transaction.execute(
        update(db.orders)
        .where(and_(db.orders.c.tenant_id == tenant_id, db.orders.c.id == "order-1"))
        .values(status="done")
    )
    transaction.execute(
        delete(db.orders).where(and_(db.orders.c.tenant_id == tenant_id, db.orders.c.id == "order-2"))
    )
""",
    )

    assert gate.collect_findings((tmp_path,), tenant_tables={"orders"}) == []


def test_tenant_write_guard_flags_dynamic_table_update_without_tenant_where(tmp_path: Path) -> None:
    _write(
        tmp_path / "repo.py",
        """
from sqlalchemy import update


def write(transaction, table):
    transaction.execute(update(table).where(table.c.id == "run-1").values(status="FAILED"))
""",
    )

    findings = gate.collect_findings((tmp_path,), tenant_tables={"runs"})

    assert [(finding.operation, finding.table) for finding in findings] == [("update", "table")]


def test_tenant_write_guard_current_repo_has_no_findings() -> None:
    assert gate.collect_findings() == []


def test_tenant_write_guard_writes_json_report(tmp_path: Path) -> None:
    _write(
        tmp_path / "repo.py",
        """
from sqlalchemy import update


def write(transaction, db):
    transaction.execute(update(db.orders).where(db.orders.c.id == "order-1").values(status="done"))
""",
    )
    findings = gate.collect_findings((tmp_path,), tenant_tables={"orders"})
    output = tmp_path / "tenant_write_guard.json"

    gate.write_report(output, findings, {"orders"})

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["gate_pass"] is False
    assert report["tenant_table_count"] == 1
    assert report["violations"][0]["table"] == "orders"
