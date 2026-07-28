"""Validate Sprint 36 MVP data-correctness evidence after the demo smoke.

The supply-chain demo proves the happy path. This gate verifies the durable
evidence left behind by that demo: expected row counts, object primary-key
uniqueness, stable reindex source hashes, and action idempotency.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import DEFAULT_TENANT_ID, demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-ci-smoke"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "mvp_data_correctness.json"
EXPECTED_DATASET_ROWS = {
    "raw.erp_orders": 3,
    "raw.crm_customers": 2,
    "clean.orders": 3,
    "clean.customers": 2,
    "ops.action_log": 1,
    "ops.order_current": 3,
}
EXPECTED_OBJECT_COUNTS = {
    "Order": 3,
    "Customer": 2,
}
DEMO_ACTION_TYPE = "ApproveOrder"
DEMO_IDEMPOTENCY_KEY = "approve-O-1001-demo"


@dataclass(frozen=True)
class DataCorrectnessFinding:
    code: str
    subject: str
    message: str
    details: dict[str, object]


def collect_findings(
    storage_root: Path = DEFAULT_STORAGE_ROOT,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> list[DataCorrectnessFinding]:
    db_path = _database_path(storage_root)
    if not db_path.exists():
        return [
            DataCorrectnessFinding(
                code="mvp_database_missing",
                subject=str(db_path),
                message="Supply-chain demo database is missing",
                details={"storageRoot": str(storage_root)},
            )
        ]
    with _connect(db_path) as conn:
        findings = [
            *_dataset_row_count_findings(conn, tenant_id),
            *_object_primary_key_findings(conn, tenant_id),
            *_action_idempotency_findings(conn, tenant_id),
        ]
    return [*findings, *_reindex_hash_findings(storage_root, tenant_id)]


def write_report(output: Path, findings: list[DataCorrectnessFinding], *, storage_root: Path, tenant_id: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "mvp_data_correctness",
        "storageRoot": _repo_relative(storage_root),
        "tenantId": tenant_id,
        "count": len(findings),
        "gatePass": not findings,
        "expectedDatasetRows": EXPECTED_DATASET_ROWS,
        "expectedObjectCounts": EXPECTED_OBJECT_COUNTS,
        "reindexHashObjectType": "Order",
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _dataset_row_count_findings(conn: sqlite3.Connection, tenant_id: str) -> list[DataCorrectnessFinding]:
    latest = _latest_dataset_rows(conn, tenant_id)
    findings: list[DataCorrectnessFinding] = []
    for dataset_ref, expected_count in EXPECTED_DATASET_ROWS.items():
        row = latest.get(dataset_ref)
        if row is None:
            findings.append(_finding("dataset_missing", dataset_ref, "Expected MVP dataset is missing", {}))
            continue
        version_count = _int_value(row["version_row_count"])
        file_count = _int_value(row["file_row_count"])
        if version_count != expected_count or file_count != expected_count:
            findings.append(
                _finding(
                    "dataset_row_count_mismatch",
                    dataset_ref,
                    "Latest dataset version/file row count does not match the MVP contract",
                    {
                        "expected": expected_count,
                        "versionRowCount": version_count,
                        "fileRowCount": file_count,
                        "versionId": _str_value(row["version_id"]),
                    },
                )
            )
        if _int_value(row["file_count"]) < 1:
            findings.append(
                _finding("dataset_file_missing", dataset_ref, "Latest dataset version has no data files", {})
            )
    return findings


def _object_primary_key_findings(conn: sqlite3.Connection, tenant_id: str) -> list[DataCorrectnessFinding]:
    rows = {
        _str_value(row["object_type_api_name"]): row
        for row in _query_rows(
            conn,
            """
            SELECT object_type_api_name,
                   COUNT(*) AS total,
                   COUNT(DISTINCT object_id) AS distinct_ids,
                   SUM(CASE WHEN object_id IS NULL OR object_id = '' THEN 1 ELSE 0 END) AS blank_ids,
                   SUM(CASE WHEN source_hash IS NULL OR source_hash = '' THEN 1 ELSE 0 END) AS missing_hashes
            FROM object_records
            WHERE tenant_id = ? AND is_active = 1 AND deleted = 0
            GROUP BY object_type_api_name
            """,
            (tenant_id,),
        )
    }
    findings: list[DataCorrectnessFinding] = []
    for object_type, expected_count in EXPECTED_OBJECT_COUNTS.items():
        row = rows.get(object_type)
        if row is None:
            findings.append(
                _finding("object_type_missing", object_type, "Expected object type has no indexed rows", {})
            )
            continue
        total = _int_value(row["total"])
        distinct_ids = _int_value(row["distinct_ids"])
        blank_ids = _int_value(row["blank_ids"])
        missing_hashes = _int_value(row["missing_hashes"])
        if total != expected_count or distinct_ids != expected_count or blank_ids or missing_hashes:
            findings.append(
                _finding(
                    "object_primary_key_mismatch",
                    object_type,
                    "Object rows must have expected counts, unique primary keys, and source hashes",
                    {
                        "expected": expected_count,
                        "total": total,
                        "distinctIds": distinct_ids,
                        "blankIds": blank_ids,
                        "missingHashes": missing_hashes,
                    },
                )
            )
    return findings


def _action_idempotency_findings(conn: sqlite3.Connection, tenant_id: str) -> list[DataCorrectnessFinding]:
    findings = [
        _finding(
            "action_idempotency_duplicate",
            f"{row['action_type_api_name']}:{row['idempotency_key']}",
            "Action idempotency key produced more than one durable action_run",
            {"count": _int_value(row["duplicate_count"]), "actorUserId": _str_value(row["actor_user_id"])},
        )
        for row in _query_rows(
            conn,
            """
            SELECT action_type_api_name, actor_user_id, idempotency_key, COUNT(*) AS duplicate_count
            FROM action_runs
            WHERE tenant_id = ?
            GROUP BY action_type_api_name, actor_user_id, idempotency_key
            HAVING COUNT(*) > 1
            """,
            (tenant_id,),
        )
    ]
    demo_rows = _query_rows(
        conn,
        """
        SELECT COUNT(*) AS run_count, GROUP_CONCAT(status) AS statuses
        FROM action_runs
        WHERE tenant_id = ? AND action_type_api_name = ? AND idempotency_key = ?
        """,
        (tenant_id, DEMO_ACTION_TYPE, DEMO_IDEMPOTENCY_KEY),
    )
    demo = demo_rows[0]
    if _int_value(demo["run_count"]) != 1 or _str_value(demo["statuses"]) != "succeeded":
        findings.append(
            _finding(
                "demo_action_idempotency_missing",
                DEMO_IDEMPOTENCY_KEY,
                "Demo ApproveOrder action must have exactly one succeeded action_run",
                {"runCount": _int_value(demo["run_count"]), "statuses": _str_value(demo["statuses"])},
            )
        )
    return findings


def _reindex_hash_findings(storage_root: Path, tenant_id: str) -> list[DataCorrectnessFinding]:
    db_path = _database_path(storage_root)
    before = _object_source_fingerprint(db_path, tenant_id, "Order")
    if not before:
        return [_finding("reindex_source_missing", "Order", "Order source hashes are missing before reindex", {})]
    with tempfile.TemporaryDirectory() as tmp:
        clone_root = Path(tmp) / "mvp-data-correctness"
        _clone_local_store_for_replay(storage_root, clone_root)
        try:
            result = FoundryLite(dependencies=create_local_core_dependencies(storage_root=clone_root)).objects.reindex(
                "Order", ctx=demo_admin_context()
            )
        except Exception as exc:
            return [
                _finding(
                    "reindex_replay_failed",
                    "Order",
                    "Order reindex replay failed on a cloned demo store",
                    {"errorType": type(exc).__name__, "message": str(exc)},
                )
            ]
        after = _object_source_fingerprint(_database_path(clone_root), tenant_id, "Order")
        rows_skipped = _index_run_rows_skipped(_database_path(clone_root), tenant_id, str(result["index_run_id"]))
    findings: list[DataCorrectnessFinding] = []
    if before != after:
        findings.append(
            _finding(
                "reindex_hash_mismatch",
                "Order",
                "Order source hashes changed after replaying the same source index",
                {"before": before, "after": after},
            )
        )
    # A same-source replay must read every row, and every row must be either
    # upserted (full rebuild) or skipped as hash-unchanged (changelog
    # incremental) — rows silently dropped in either mode are a correctness bug.
    if result["rows_read"] != 3 or result["objects_upserted"] + rows_skipped != 3:
        findings.append(
            _finding(
                "reindex_count_mismatch",
                "Order",
                "Order reindex replay did not read/upsert-or-skip the expected rows",
                {
                    "rowsRead": result["rows_read"],
                    "objectsUpserted": result["objects_upserted"],
                    "rowsSkipped": rows_skipped,
                },
            )
        )
    return findings


def _clone_local_store_for_replay(storage_root: Path, clone_root: Path) -> None:
    """Copy a local store and relocate every durable Dataset URI into the copy."""

    source_root = storage_root.resolve()
    target_root = clone_root.resolve()
    shutil.copytree(source_root, target_root)
    with _connect(_database_path(target_root)) as conn:
        manifest_rows = _query_rows(conn, "SELECT id, manifest_uri FROM dataset_versions", ())
        for row in manifest_rows:
            source_uri = _str_value(row["manifest_uri"])
            clone_uri = _relocated_clone_uri(source_uri, source_root, target_root)
            _rewrite_cloned_manifest(Path(clone_uri), source_root, target_root)
            conn.execute(
                "UPDATE dataset_versions SET manifest_uri = ? WHERE id = ?",
                (clone_uri, _str_value(row["id"])),
            )
        file_rows = _query_rows(conn, "SELECT id, uri FROM dataset_files", ())
        for row in file_rows:
            conn.execute(
                "UPDATE dataset_files SET uri = ? WHERE id = ?",
                (
                    _relocated_clone_uri(_str_value(row["uri"]), source_root, target_root),
                    _str_value(row["id"]),
                ),
            )


def _rewrite_cloned_manifest(manifest_path: Path, source_root: Path, target_root: Path) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        raise ValueError(f"cloned dataset manifest has invalid files: {manifest_path}")
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
            raise ValueError(f"cloned dataset manifest has invalid file URI: {manifest_path}")
        item["uri"] = _relocated_clone_uri(item["uri"], source_root, target_root)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _relocated_clone_uri(uri: str, source_root: Path, target_root: Path) -> str:
    path = Path(uri)
    if not path.is_absolute():
        return uri
    try:
        relative = path.resolve().relative_to(source_root)
    except ValueError:
        return uri
    return str(target_root / relative)


def _index_run_rows_skipped(db_path: Path, tenant_id: str, index_run_id: str) -> int:
    """Read the changelog-incremental skip count recorded on the run's source_ref."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = _query_rows(
            conn,
            "SELECT source_ref FROM index_runs WHERE tenant_id = ? AND id = ?",
            (tenant_id, index_run_id),
        )
    if not rows:
        return 0
    source_ref = json.loads(str(rows[0]["source_ref"]) or "{}")
    skipped = source_ref.get("skipped", 0)
    return skipped if isinstance(skipped, int) else 0


def _latest_dataset_rows(conn: sqlite3.Connection, tenant_id: str) -> dict[str, sqlite3.Row]:
    rows = _query_rows(
        conn,
        """
        SELECT d.namespace || '.' || d.name AS dataset_ref,
               dv.id AS version_id,
               dv.version_number AS version_number,
               dv.row_count AS version_row_count,
               COALESCE(SUM(df.row_count), 0) AS file_row_count,
               COUNT(df.id) AS file_count
        FROM datasets d
        JOIN dataset_versions dv ON dv.dataset_id = d.id AND dv.tenant_id = d.tenant_id
        LEFT JOIN dataset_files df ON df.dataset_version_id = dv.id AND df.tenant_id = dv.tenant_id
        WHERE d.tenant_id = ?
          AND dv.version_number = (
            SELECT MAX(dv2.version_number)
            FROM dataset_versions dv2
            WHERE dv2.dataset_id = d.id AND dv2.tenant_id = d.tenant_id
          )
        GROUP BY d.namespace, d.name, dv.id, dv.version_number, dv.row_count
        """,
        (tenant_id,),
    )
    return {_str_value(row["dataset_ref"]): row for row in rows}


def _object_source_fingerprint(db_path: Path, tenant_id: str, object_type: str) -> dict[str, str]:
    with _connect(db_path) as conn:
        rows = _query_rows(
            conn,
            """
            SELECT object_id, source_dataset_version_id, source_hash
            FROM object_records
            WHERE tenant_id = ? AND object_type_api_name = ? AND is_active = 1 AND deleted = 0
            ORDER BY object_id
            """,
            (tenant_id, object_type),
        )
    return {
        _str_value(row["object_id"]): f"{_str_value(row['source_dataset_version_id'])}:{_str_value(row['source_hash'])}"
        for row in rows
    }


def _query_rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _database_path(storage_root: Path) -> Path:
    return storage_root / "foundry-lite.db"


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else int(str(value or 0))


def _str_value(value: object) -> str:
    return value if isinstance(value, str) else str(value or "")


def _finding(code: str, subject: str, message: str, details: dict[str, object]) -> DataCorrectnessFinding:
    return DataCorrectnessFinding(code=code, subject=subject, message=message, details=details)


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _print_failure(findings: list[DataCorrectnessFinding], output: Path) -> None:
    print("MVP data-correctness gate failed:")
    for finding in findings:
        print(f"- {finding.subject} {finding.code}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate MVP release data-correctness evidence.")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    findings = collect_findings(args.storage_root, tenant_id=args.tenant_id)
    write_report(args.output, findings, storage_root=args.storage_root, tenant_id=args.tenant_id)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print(f"MVP data-correctness gate passed: report={_repo_relative(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
