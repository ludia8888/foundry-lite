"""Independent issuer-principal policy for protected release evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class ReleaseActionIdentity(Protocol):
    @property
    def tool_name(self) -> str: ...

    @property
    def mcp_session_id(self) -> str: ...


class ReleasePrincipalClaim(Protocol):
    @property
    def submitter_subject_hash(self) -> str: ...

    @property
    def submitter_oauth_session_hash(self) -> str: ...

    @property
    def reviewer_subject_hash(self) -> str: ...

    @property
    def reviewer_oauth_session_hash(self) -> str: ...

    @property
    def actions(self) -> Sequence[ReleaseActionIdentity]: ...


def separation_blockers(claim: ReleasePrincipalClaim) -> tuple[str, ...]:
    blockers: list[str] = []
    if claim.submitter_subject_hash == claim.reviewer_subject_hash:
        blockers.append("submitter_reviewer_subjects_must_be_distinct")
    if claim.submitter_oauth_session_hash == claim.reviewer_oauth_session_hash:
        blockers.append("submitter_reviewer_oauth_sessions_must_be_distinct")
    submitter_sessions = _role_sessions(claim.actions, is_submitter=True)
    reviewer_sessions = _role_sessions(claim.actions, is_submitter=False)
    if submitter_sessions & reviewer_sessions:
        blockers.append("submitter_reviewer_mcp_sessions_overlap")
    return tuple(blockers)


def _role_sessions(actions: Sequence[ReleaseActionIdentity], *, is_submitter: bool) -> set[str]:
    return {
        action.mcp_session_id for action in actions if (action.tool_name == "publish_release_candidate") is is_submitter
    }


__all__ = ["separation_blockers"]
