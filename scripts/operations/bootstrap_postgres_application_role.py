"""Create the bounded non-superuser PostgreSQL role used by protected runtimes."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any

import psycopg
from psycopg import sql

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_LOCK_KEY = 7_301_554_201


def bootstrap(
    role: str,
    database: str,
    environment: Mapping[str, str] = os.environ,
    connector: Callable[..., Any] = psycopg.connect,
) -> dict[str, object]:
    role = _identifier(role, "postgres_application_role_invalid")
    database = _identifier(database, "postgres_application_database_invalid")
    admin_dsn = _admin_dsn(_required(environment, "FOUNDRY_LITE_DB_URL"))
    password = _required(environment, "POSTGRES_APP_PASSWORD")
    if len(password) < 24:
        raise ValueError("postgres_application_password_invalid")
    with connector(admin_dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            status = _reconcile_role(cursor, role, password)
            _grant_runtime_privileges(cursor, role, database)
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return {
        "schemaVersion": 1,
        "status": status,
        "role": role,
        "database": database,
        "isSuperuser": False,
        "canBypassRls": False,
        "rawCredentialStored": False,
    }


def _reconcile_role(cursor: Any, role: str, password: str) -> str:
    flags = cursor.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    if flags is not None and any(bool(value) for value in flags):
        raise RuntimeError("postgres_application_role_is_privileged")
    identifier = sql.Identifier(role)
    password_literal = sql.Literal(password)
    if flags is None:
        cursor.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(identifier, password_literal)
        )
        return "created"
    cursor.execute(
        sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
            identifier, password_literal
        ),
    )
    return "updated"


def _grant_runtime_privileges(cursor: Any, role: str, database: str) -> None:
    role_identifier = sql.Identifier(role)
    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), role_identifier))
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role_identifier))
    cursor.execute(
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role_identifier)
    )
    cursor.execute(
        sql.SQL("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}").format(role_identifier)
    )
    cursor.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(role_identifier)
    )
    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}").format(
            role_identifier
        )
    )


def _admin_dsn(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgresql://"):
        return value
    raise ValueError("postgres_application_admin_dsn_invalid")


def _identifier(value: str, reason: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None or (reason.endswith("role_invalid") and value == "postgres"):
        raise ValueError(reason)
    return value


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key, "").strip()
    if not value or len(value) > 2048:
        raise ValueError(f"postgres_application_environment_invalid:{key}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--database", required=True)
    receipt = bootstrap(**vars(parser.parse_args(argv)))
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
