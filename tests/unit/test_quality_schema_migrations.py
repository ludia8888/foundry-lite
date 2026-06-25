from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_schema_migrations as gate


def _write_migration(
    versions_dir: Path,
    filename: str,
    *,
    revision: str,
    down_revision: str | None,
    migration_phase: str | None = None,
    include_phase: bool = True,
    release_compatibility: str | None = None,
    include_release_compatibility: bool = True,
    upgrade_body: str = "pass",
    downgrade_body: str = "raise NotImplementedError('forward fix only')",
) -> Path:
    down_value = repr(down_revision) if down_revision is not None else "None"
    phase = migration_phase or ("baseline" if down_revision is None else "expand")
    phase_line = f"migration_phase: str = {phase!r}\n" if include_phase else ""
    window = release_compatibility or ("bootstrap" if phase == "baseline" else "old_and_new_app")
    window_line = f"release_compatibility: str = {window!r}\n" if include_release_compatibility else ""
    path = versions_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'''"""test migration"""

import sqlalchemy as sa
from alembic import op

revision: str = {revision!r}
down_revision: str | None = {down_value}
{phase_line}\
{window_line}\
branch_labels = None
depends_on = None


def upgrade() -> None:
    {upgrade_body}


def downgrade() -> None:
    {downgrade_body}
''',
        encoding="utf-8",
    )
    return path


def test_schema_migration_gate_passes_linear_forward_fix_chain(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(versions_dir, "bbb222_child.py", revision="bbb222", down_revision="aaa111")

    assert gate.collect_findings(versions_dir=versions_dir) == []


def test_schema_migration_gate_flags_multiple_heads(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(versions_dir, "bbb222_child.py", revision="bbb222", down_revision="aaa111")
    _write_migration(versions_dir, "ccc333_other_child.py", revision="ccc333", down_revision="aaa111")

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_head_count_invalid" for finding in findings)


def test_schema_migration_gate_flags_destructive_downgrade(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(
        versions_dir,
        "aaa111_root.py",
        revision="aaa111",
        down_revision=None,
        downgrade_body='op.drop_table("orders")',
    )

    findings = gate.collect_findings(versions_dir=versions_dir)
    codes = {finding.code for finding in findings}

    assert "migration_downgrade_not_forward_fix_only" in codes
    assert "migration_downgrade_uses_schema_ops" in codes


def test_schema_migration_gate_requires_phase_metadata(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(
        versions_dir,
        "aaa111_root.py",
        revision="aaa111",
        down_revision=None,
        include_phase=False,
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_phase_missing" for finding in findings)


def test_release_compatibility_window_required(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(
        versions_dir,
        "aaa111_root.py",
        revision="aaa111",
        down_revision=None,
        include_release_compatibility=False,
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_release_compatibility_missing" for finding in findings)


def test_expand_phase_requires_old_and_new_app_window(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        release_compatibility="new_app_only",
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_release_compatibility_mismatch" for finding in findings)


def test_old_app_reads_expand_phase_schema(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body='op.add_column("orders", sa.Column("status", sa.String(), nullable=False, server_default="new"))',
    )

    assert gate.collect_findings(versions_dir=versions_dir) == []


def test_expand_phase_blocks_destructive_upgrade(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body='op.drop_column("orders", "legacy_status")',
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_expand_operation_not_allowed" for finding in findings)


def test_expand_phase_allows_safe_rls_upgrade_sql(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body='op.execute("ALTER TABLE ai_sessions ENABLE ROW LEVEL SECURITY")',
    )

    assert gate.collect_findings(versions_dir=versions_dir) == []


def test_expand_phase_requires_ai_tenant_table_rls(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body=(
            'op.create_table("ai_shadow_runs", '
            'sa.Column("id", sa.String(), nullable=False), '
            'sa.Column("tenant_id", sa.String(), nullable=False))'
        ),
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_ai_tenant_rls_missing" for finding in findings)


def test_expand_phase_accepts_ai_tenant_table_rls_policy(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body=(
            'op.create_table("ai_shadow_runs", '
            'sa.Column("id", sa.String(), nullable=False), '
            'sa.Column("tenant_id", sa.String(), nullable=False))\n'
            '    op.execute("ALTER TABLE ai_shadow_runs ENABLE ROW LEVEL SECURITY")\n'
            '    op.execute("ALTER TABLE ai_shadow_runs FORCE ROW LEVEL SECURITY")\n'
            '    op.execute("CREATE POLICY ai_shadow_runs_tenant_isolation ON ai_shadow_runs '
            "USING (tenant_id = current_setting('foundry_lite.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))"
            '")'
        ),
    )

    assert gate.collect_findings(versions_dir=versions_dir) == []


def test_expand_phase_blocks_destructive_upgrade_sql(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body='op.execute("ALTER TABLE orders DROP COLUMN legacy_status")',
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_expand_execute_destructive_sql" for finding in findings)


def test_expand_phase_requires_default_for_not_null_column(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        upgrade_body='op.add_column("orders", sa.Column("required_code", sa.String(), nullable=False))',
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_expand_not_null_column_without_default" for finding in findings)


def test_contract_phase_rejects_old_writer(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="aaa111", down_revision=None)
    _write_migration(
        versions_dir,
        "bbb222_child.py",
        revision="bbb222",
        down_revision="aaa111",
        migration_phase="contract",
        release_compatibility="new_app_only",
        upgrade_body='op.drop_column("orders", "legacy_status")',
    )

    findings = gate.collect_findings(versions_dir=versions_dir)

    assert any(finding.code == "migration_contract_phase_requires_old_writer_gate" for finding in findings)


def test_schema_migration_gate_writes_json_report(tmp_path: Path) -> None:
    versions_dir = tmp_path / "migrations" / "versions"
    _write_migration(versions_dir, "aaa111_root.py", revision="wrong", down_revision=None)
    findings = gate.collect_findings(versions_dir=versions_dir)
    output = tmp_path / "artifacts" / "quality" / "schema_migrations.json"

    gate.write_report(output, findings, versions_dir=versions_dir)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate"] == "schema_migrations"
    assert report["gate_pass"] is False
    assert report["count"] == len(findings)
