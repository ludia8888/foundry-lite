from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.operations import bootstrap_postgres_application_role as subject


class _Cursor:
    def __init__(self, flags: tuple[bool, ...] | None = None) -> None:
        self.flags = flags
        self.calls: list[tuple[object, object]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, parameters: object = None) -> _Cursor:
        self.calls.append((statement, parameters))
        return self

    def fetchone(self) -> tuple[bool, ...] | None:
        return self.flags


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def _environment() -> dict[str, str]:
    return {
        "FOUNDRY_LITE_DB_URL": "postgresql+psycopg://postgres:admin-secret@postgres/foundry_lite",
        "POSTGRES_APP_PASSWORD": "runtime-secret-password-value-123456789",
    }


def test_bootstrap_creates_a_non_privileged_runtime_role_without_leaking_credentials() -> None:
    cursor = _Cursor()
    observed: dict[str, Any] = {}

    def connect(dsn: str, **kwargs: object) -> _Connection:
        observed.update(dsn=dsn, kwargs=kwargs)
        return _Connection(cursor)

    receipt = subject.bootstrap("foundry_lite_app", "foundry_lite", _environment(), connect)

    assert receipt["status"] == "created"
    assert receipt["isSuperuser"] is False
    assert receipt["canBypassRls"] is False
    assert receipt["rawCredentialStored"] is False
    assert "admin-secret" not in json.dumps(receipt)
    assert "runtime-secret" not in json.dumps(receipt)
    assert observed["dsn"].startswith("postgresql://postgres:")
    assert observed["kwargs"] == {"autocommit": True}
    assert len(cursor.calls) == 10
    create_role_statement, create_role_parameters = cursor.calls[2]
    assert create_role_parameters is None
    assert "PASSWORD $1" not in create_role_statement.as_string(None)


def test_bootstrap_quotes_role_password_as_a_postgres_literal() -> None:
    cursor = _Cursor()
    environment = _environment() | {"POSTGRES_APP_PASSWORD": "runtime-'quoted'-password-value-123456789"}

    subject.bootstrap(
        "foundry_lite_app",
        "foundry_lite",
        environment,
        lambda *_args, **_kwargs: _Connection(cursor),
    )

    statement, parameters = cursor.calls[2]
    rendered = statement.as_string(None)
    assert parameters is None
    assert "PASSWORD 'runtime-''quoted''-password-value-123456789'" in rendered


def test_bootstrap_rejects_an_existing_privileged_role() -> None:
    cursor = _Cursor((True, False, False, False, False))

    with pytest.raises(RuntimeError, match="role_is_privileged"):
        subject.bootstrap(
            "foundry_lite_app",
            "foundry_lite",
            _environment(),
            lambda *_args, **_kwargs: _Connection(cursor),
        )


@pytest.mark.parametrize("value", ("postgres", "Foundry_App", "bad-role", "a" * 64))
def test_bootstrap_rejects_unapproved_role_names(value: str) -> None:
    with pytest.raises(ValueError, match="role_invalid"):
        subject.bootstrap(value, "foundry_lite", _environment())


def test_main_prints_only_the_safe_receipt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        subject,
        "bootstrap",
        lambda **_kwargs: {"status": "created", "rawCredentialStored": False},
    )

    assert subject.main(["--role", "foundry_lite_app", "--database", "foundry_lite"]) == 0
    assert json.loads(capsys.readouterr().out) == {"rawCredentialStored": False, "status": "created"}
