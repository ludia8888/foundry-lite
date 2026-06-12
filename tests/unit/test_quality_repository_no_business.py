from __future__ import annotations

from pathlib import Path

from scripts.quality import check_repository_no_business as gate


def _write_repository_file(tmp_path: Path, source: str) -> Path:
    repository_file = tmp_path / "libs" / "foundry_lite" / "infrastructure" / "repositories" / "example_repository.py"
    repository_file.parent.mkdir(parents=True)
    repository_file.write_text(source, encoding="utf-8")
    return repository_file


def test_repository_no_business_flags_domain_error_import_and_raise(tmp_path: Path) -> None:
    repository_file = _write_repository_file(
        tmp_path,
        """
from foundry_lite.domain.errors import ConflictDetected


def save(record):
    if record["version"] < 0:
        raise ConflictDetected("object version conflict")
""",
    )

    findings = gate.collect_findings((repository_file,))

    assert [finding.code for finding in findings] == [
        "repository_imports_domain_error",
        "repository_raises_domain_error",
    ]
    assert findings[1].expression == "ConflictDetected"


def test_repository_no_business_flags_direct_commit_or_rollback(tmp_path: Path) -> None:
    repository_file = _write_repository_file(
        tmp_path,
        """
def save(transaction):
    transaction.commit()
    transaction.rollback()
""",
    )

    findings = gate.collect_findings((repository_file,))

    assert [finding.code for finding in findings] == [
        "repository_controls_transaction",
        "repository_controls_transaction",
    ]


def test_repository_no_business_allows_port_error_and_savepoint_control(tmp_path: Path) -> None:
    repository_file = _write_repository_file(
        tmp_path,
        """
from foundry_lite.application.ports import DatasetAlreadyExistsError


def save(transaction):
    savepoint = transaction.begin_nested()
    try:
        transaction.execute("insert")
    except Exception as exc:
        savepoint.rollback()
        raise DatasetAlreadyExistsError from exc
    savepoint.commit()
""",
    )

    assert gate.collect_findings((repository_file,)) == []


def test_repository_no_business_writes_json_report(tmp_path: Path) -> None:
    repository_file = _write_repository_file(
        tmp_path,
        """
from foundry_lite.domain.errors import PermissionDenied
""",
    )
    output = tmp_path / "quality" / "repository_no_business.json"
    findings = gate.collect_findings((repository_file,))

    gate.write_report(output, findings)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"repository_imports_domain_error"' in report
