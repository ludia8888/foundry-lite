from __future__ import annotations

from pathlib import Path

import pytest

from scripts.quality import check_transaction_outbox_pair as gate


def _write_service(tmp_path: Path, source: str) -> Path:
    services_root = tmp_path / "services"
    services_root.mkdir()
    path = services_root / "order_service.py"
    path.write_text(source, encoding="utf-8")
    return services_root


def test_transaction_outbox_pair_flags_write_without_audit_or_outbox(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        with self.engine.begin() as conn:
            self.order_repository.insert_order(transaction=conn)
""",
    )

    findings = gate.collect_findings(services_root=services_root, project_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].method == "create_order"
    assert findings[0].write_calls == ("self.order_repository.insert_order",)


def test_transaction_outbox_pair_accepts_direct_audit_in_same_transaction(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        with self.engine.begin() as conn:
            self.order_repository.insert_order(transaction=conn)
            self.runtime_service._audit(conn, None, event_type="order.created")
""",
    )

    assert gate.collect_findings(services_root=services_root, project_root=tmp_path) == []


def test_transaction_outbox_pair_accepts_delegated_outbox_helper(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class DatasetTransactionService:
    def _finalize(self, conn):
        self.dataset_repository.commit_dataset(transaction=conn)
        self.runtime_service._outbox(conn, None, "dataset.version.committed", "dataset", "ds1", {})

class IngestService:
    def upload_csv(self):
        with self.engine.begin() as conn:
            self.dataset_transaction_service._finalize(conn)
""",
    )

    assert gate.collect_findings(services_root=services_root, project_root=tmp_path) == []


def test_transaction_outbox_pair_does_not_borrow_audit_from_unrelated_same_name_method(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class UnrelatedService:
    def _persist(self, conn):
        self.runtime_service._audit(conn, None, event_type="unrelated.updated")

class OrderService:
    def _persist(self, conn):
        self.order_repository.insert_order(transaction=conn)

    def create_order(self):
        with self.engine.begin() as conn:
            self._persist(conn)
""",
    )

    findings = gate.collect_findings(services_root=services_root, project_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].write_calls == ("self.order_repository.insert_order",)


def test_transaction_outbox_pair_reuses_repeated_delegation_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services_root = _write_service(
        tmp_path,
        """
class DatasetTransactionService:
    def _finalize(self, conn):
        self.dataset_repository.commit_dataset(transaction=conn)
        self.runtime_service._outbox(conn, None, "dataset.version.committed", "dataset", "ds1", {})

class IngestService:
    def upload_csv(self):
        with self.engine.begin() as conn:
            self.dataset_transaction_service._finalize(conn)
            self.dataset_transaction_service._finalize(conn)
""",
    )
    original = gate._analyze_statements
    analyzed_classes: list[str] = []

    def counting_analyze(**kwargs):
        analyzed_classes.append(kwargs["class_name"])
        return original(**kwargs)

    monkeypatch.setattr(gate, "_analyze_statements", counting_analyze)

    assert gate.collect_findings(services_root=services_root, project_root=tmp_path) == []
    assert analyzed_classes.count("DatasetTransactionService") == 1


def test_transaction_outbox_pair_requires_proof_inside_transaction_call_tree(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        with self.engine.begin() as conn:
            self.order_repository.insert_order(transaction=conn)
        self.runtime_service._audit(conn, None, event_type="order.created")
""",
    )

    findings = gate.collect_findings(services_root=services_root, project_root=tmp_path)

    assert [finding.message for finding in findings] == [
        "transaction-scoped repository writes need audit/outbox proof in the same call tree"
    ]


def test_transaction_outbox_pair_allows_preparatory_lifecycle_writes(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class IngestService:
    def upload_csv(self):
        with self.engine.begin() as conn:
            self.dataset_transaction_repository.create_open_transaction(transaction=conn)
            self.dataset_transaction_repository.insert_sync_run(transaction=conn)
""",
    )

    assert gate.collect_findings(services_root=services_root, project_root=tmp_path) == []


def test_transaction_outbox_pair_writes_json_report(tmp_path: Path) -> None:
    services_root = _write_service(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        with self.engine.begin() as conn:
            self.order_repository.insert_order(transaction=conn)
""",
    )
    output = tmp_path / "quality" / "transaction_outbox_pair.json"
    findings = gate.collect_findings(services_root=services_root, project_root=tmp_path)

    gate.write_report(output, findings)

    report = output.read_text(encoding="utf-8")
    assert '"gate": "transaction_outbox_pair"' in report
    assert '"gate_pass": false' in report
    assert '"count": 1' in report


def test_evidence_is_cached_per_method_not_per_path() -> None:
    """Keying the cache on the route that reached a method made the walk exponential.

    A call like `self.runtime_service._audit(...)` cannot be resolved to one class, so it fans
    out to every method of that name. With the caller's `seen` set in the key, the same method
    was re-analysed once per distinct path to it, and the cost grew with the number of paths
    rather than the number of methods: adding one more `_audit` definition took this gate from
    5s to past its 415s lane ceiling. Evidence for a method under a given transaction parameter
    does not depend on who called it.
    """
    assert gate.EvidenceCacheKey is gate.MethodCallKey


def test_the_gate_still_passes_on_this_repository() -> None:
    """The cache change must not have bought speed by losing findings."""
    findings = gate.collect_findings()

    assert findings == []
