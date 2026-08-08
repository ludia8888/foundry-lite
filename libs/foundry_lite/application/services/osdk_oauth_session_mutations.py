"""Atomic compare-and-set helpers for OSDK OAuth session mutations."""

from __future__ import annotations

from foundry_lite.application.ports import OAuthSessionRepository, TransactionContext
from foundry_lite.domain.errors import ConflictDetected


def _consume_code_once(
    repository: OAuthSessionRepository,
    transaction: TransactionContext,
    tenant_id: str,
    code_id: str,
    consumed_at: str,
) -> None:
    consumed = repository.mark_authorization_code_consumed(
        transaction=transaction,
        tenant_id=tenant_id,
        code_id=code_id,
        consumed_at=consumed_at,
    )
    if not consumed:
        raise ConflictDetected("OSDK OAuth authorization code was already consumed")


def _rotate_refresh_once(
    repository: OAuthSessionRepository,
    transaction: TransactionContext,
    tenant_id: str,
    token_id: str,
    replacement_token_id: str,
    session_id: str,
    used_at: str,
) -> None:
    rotated = repository.rotate_refresh_token(
        transaction=transaction,
        tenant_id=tenant_id,
        token_id=token_id,
        replacement_token_id=replacement_token_id,
        used_at=used_at,
    )
    if not rotated:
        raise ConflictDetected("OSDK OAuth refresh token was already rotated")
    repository.update_session_refresh(
        transaction=transaction,
        tenant_id=tenant_id,
        session_id=session_id,
        refreshed_at=used_at,
    )
