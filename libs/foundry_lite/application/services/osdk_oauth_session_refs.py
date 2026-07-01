"""Application service helpers for osdk oauth session refs workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import OAuthAuthorizationCodeRow, RuntimeJsonObject


def safe_code_ref(row: OAuthAuthorizationCodeRow, state: str | None) -> RuntimeJsonObject:
    return {
        "codeId": row["id"],
        "appId": row["app_id"],
        "clientId": row["client_id"],
        "redirectUri": row["redirect_uri"],
        "scopes": row["scopes"],
        "expiresAt": row["expires_at"],
        "statePresent": state is not None,
    }


def safe_session_ref(session: Mapping[str, object]) -> RuntimeJsonObject:
    return {
        "sessionId": session["id"],
        "appId": session["app_id"],
        "clientId": session["client_id"],
        "actorUserId": session["actor_user_id"],
        "scopes": cast(Sequence[object], session["scopes"]),
        "status": session["status"],
    }
