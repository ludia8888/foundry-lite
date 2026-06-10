from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from foundry_lite.infrastructure import schema as db


class SqlAlchemyMetadataRepository:
    """SQLAlchemy implementation for bootstrap metadata lifecycle."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def initialize_schema(self) -> None:
        db.create_database(self.engine)

    def reset_schema(self) -> None:
        db.metadata.drop_all(self.engine)
        db.create_database(self.engine)

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
            if existing is None:
                conn.execute(
                    insert(db.users).values(
                        id=user_id,
                        tenant_id=tenant_id,
                        email=email,
                        roles=roles,
                        created_at=created_at,
                    )
                )
