from __future__ import annotations

from pathlib import Path

from scripts.quality import check_idempotency_on_action as gate


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _write_contract(
    root: Path,
    *,
    api_header: bool = True,
    lookup_before_insert: bool = True,
    schema_unique: bool = True,
) -> None:
    header_param = (
        'idempotency_key: str = Header(alias="Idempotency-Key")' if api_header else 'idempotency_key: str = ""'
    )
    lookup_block = (
        """
        existing = self._existing_action_run(conn, ctx, action_type, idempotency_key)
        if existing is not None:
            return self._action_replay_response(existing)
        self._insert_action_run(conn, ctx, idempotency_key=idempotency_key)
"""
        if lookup_before_insert
        else """
        self._insert_action_run(conn, ctx, idempotency_key=idempotency_key)
        existing = self._existing_action_run(conn, ctx, action_type, idempotency_key)
"""
    )
    schema_constraint = (
        """
    UniqueConstraint(
        "tenant_id",
        "action_type_id",
        "actor_user_id",
        "idempotency_key",
        name="uq_action_idempotency",
    ),
"""
        if schema_unique
        else ""
    )
    _write(
        root / "apps" / "api" / "foundry_lite_api" / "main.py",
        f"""
def Header(*, alias):
    return alias


def apply_action(request, action_type: str, payload, {header_param}):
    return core.apply_action(action_type, idempotency_key=idempotency_key)
""",
    )
    _write(
        root / "libs" / "foundry_lite" / "application" / "core.py",
        """
class FoundryLiteCore:
    def apply_action(self, action_api_name: str, *, idempotency_key: str):
        return self._services.action.apply_action(action_api_name, idempotency_key=idempotency_key)
""",
    )
    _write(
        root / "libs" / "foundry_lite" / "application" / "services" / "action_service.py",
        f"""
class ValidationFailed(Exception):
    pass


class ActionRunRecord:
    def __init__(self, *, idempotency_key):
        self.idempotency_key = idempotency_key


class ActionService:
    def apply_action(self, action_api_name: str, *, idempotency_key: str):
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        with self.engine.begin() as conn:
            action_type = {{"id": "action_type_1"}}
{lookup_block}
        return {{"status": "succeeded"}}

    def _existing_action_run(self, conn, ctx, action_type, idempotency_key):
        return self.action_repository.action_run_by_idempotency(
            transaction=conn,
            idempotency_key=idempotency_key,
        )

    def _action_replay_response(self, existing):
        return {{"idempotentReplay": True}}

    def _insert_action_run(self, conn, ctx, *, idempotency_key):
        return ActionRunRecord(idempotency_key=idempotency_key)
""",
    )
    _write(
        root / "libs" / "foundry_lite" / "application" / "ports" / "action_repository.py",
        """
class ActionRepository:
    def action_run_by_idempotency(self, *, idempotency_key: str):
        ...
""",
    )
    _write(
        root / "libs" / "foundry_lite" / "infrastructure" / "schema.py",
        f"""
def Table(*args):
    return args


def UniqueConstraint(*args, **kwargs):
    return args, kwargs


action_runs = Table(
    "action_runs",
{schema_constraint}
)
""",
    )


def test_action_idempotency_gate_accepts_complete_contract(tmp_path: Path) -> None:
    _write_contract(tmp_path)

    assert gate.collect_findings(tmp_path) == []


def test_action_idempotency_gate_accepts_delegated_service_contract(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "services" / "action_helpers.py",
        """
class ValidationFailed(Exception):
    pass


class Command:
    def __init__(self, *, idempotency_key):
        self.idempotency_key = idempotency_key


def action_command(action_api_name, object_type, object_id, expected_object_version, params, idempotency_key, simulate):
    if not idempotency_key:
        raise ValidationFailed("idempotency key is required")
    return Command(idempotency_key=idempotency_key)


def action_replay_response(existing):
    return {"idempotentReplay": True}
""",
    )
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "services" / "action_service.py",
        """
from .action_helpers import action_command, action_replay_response


class ActionRunRecord:
    def __init__(self, *, idempotency_key):
        self.idempotency_key = idempotency_key


class ActionService:
    def apply_action(self, action_api_name: str, *, idempotency_key: str):
        command = action_command(action_api_name, "Order", "order-1", 1, {}, idempotency_key, False)
        with self.engine.begin() as conn:
            return self._run_action_command(conn, None, command)

    def _run_action_command(self, conn, ctx, command):
        action_type = {"id": "action_type_1"}
        existing = self._existing_action_run(conn, ctx, action_type, command.idempotency_key)
        if existing is not None:
            return action_replay_response(existing)
        self._insert_action_run(conn, ctx, idempotency_key=command.idempotency_key)
        return {"status": "succeeded"}

    def _existing_action_run(self, conn, ctx, action_type, idempotency_key):
        return self.action_repository.action_run_by_idempotency(
            transaction=conn,
            idempotency_key=idempotency_key,
        )

    def _insert_action_run(self, conn, ctx, *, idempotency_key):
        return ActionRunRecord(idempotency_key=idempotency_key)
""",
    )

    assert gate.collect_findings(tmp_path) == []


def test_action_idempotency_gate_flags_missing_api_header(tmp_path: Path) -> None:
    _write_contract(tmp_path, api_header=False)

    findings = gate.collect_findings(tmp_path)

    assert [finding.code for finding in findings] == ["api_idempotency_header_missing"]


def test_action_idempotency_gate_flags_lookup_after_insert(tmp_path: Path) -> None:
    _write_contract(tmp_path, lookup_before_insert=False)

    findings = gate.collect_findings(tmp_path)

    assert [finding.code for finding in findings] == [
        "service_existing_run_lookup_after_insert",
        "service_replay_response_missing",
    ]


def test_action_idempotency_gate_flags_missing_unique_constraint(tmp_path: Path) -> None:
    _write_contract(tmp_path, schema_unique=False)

    findings = gate.collect_findings(tmp_path)

    assert [finding.code for finding in findings] == ["schema_action_idempotency_unique_constraint_missing"]


def test_action_idempotency_gate_writes_json_report(tmp_path: Path) -> None:
    _write_contract(tmp_path, schema_unique=False)
    output = tmp_path / "quality" / "action_idempotency.json"
    findings = gate.collect_findings(tmp_path)

    gate.write_report(output, findings)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"schema_action_idempotency_unique_constraint_missing"' in report
