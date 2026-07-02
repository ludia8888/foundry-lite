"""SQLAlchemy repository adapter for metadata repository persistence."""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.infrastructure import schema as db


class SqlAlchemyMetadataRepository:
    """SQLAlchemy implementation for non-destructive metadata bootstrap."""

    def __init__(self, engine: Engine, *, allow_schema_mutation: bool = True) -> None:
        self.engine = engine
        self.allow_schema_mutation = allow_schema_mutation

    def initialize_schema(self) -> None:
        self._require_schema_mutation_allowed("initialize_schema")
        db.create_database(self.engine)

    def _require_schema_mutation_allowed(self, operation: str) -> None:
        if self.allow_schema_mutation:
            return
        raise SchemaMutationDisabledError(
            f"metadata repository {operation} is disabled for this runtime profile; run Alembic migrations instead"
        )

    def ensure_tenant(self, *, tenant_id: str, name: str, created_at: str) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(select(db.tenants.c.id).where(db.tenants.c.id == tenant_id)).first()
            if existing is None:
                conn.execute(
                    insert(db.tenants).values(
                        id=tenant_id,
                        name=name,
                        created_at=created_at,
                    )
                )

    def ensure_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        email: str,
        roles: list[str],
        created_at: str,
    ) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(select(db.users.c.id).where(db.users.c.id == user_id)).first()
            if existing is not None:
                return
            # check-then-insert races when two processes bootstrap against the
            # same database concurrently (e.g. parallel test collection each
            # importing the API module's module-level FoundryLite singleton).
            # A savepoint-guarded insert makes ensure_user idempotent under that
            # race: a duplicate loses the insert and is treated as already-present.
            savepoint = conn.begin_nested()
            try:
                conn.execute(
                    insert(db.users).values(
                        id=user_id,
                        tenant_id=tenant_id,
                        email=email,
                        roles=roles,
                        created_at=created_at,
                    )
                )
                savepoint.commit()
            except IntegrityError:
                savepoint.rollback()


class SqlAlchemyDestructiveDevelopmentAdmin:
    """SQLAlchemy implementation for explicit development-only schema reset."""

    def __init__(self, engine: Engine, *, allow_schema_mutation: bool = True) -> None:
        self.engine = engine
        self.allow_schema_mutation = allow_schema_mutation

    def reset_schema(self) -> None:
        self._require_schema_mutation_allowed("reset_schema")
        db.metadata.drop_all(self.engine)
        db.create_database(self.engine)

    def _require_schema_mutation_allowed(self, operation: str) -> None:
        if self.allow_schema_mutation:
            return
        raise SchemaMutationDisabledError(
            f"destructive development admin {operation} is disabled for this runtime profile; "
            "run Alembic migrations instead"
        )


class SchemaMutationDisabledError(RuntimeError):
    """Raised when app/runtime code tries to mutate schema outside dev/test."""
