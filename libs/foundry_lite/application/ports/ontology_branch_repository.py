"""Application port contract for ontology branch persistence.

An ontology branch is an isolated working copy of the ontology definition:
``base_yaml_text`` snapshots main at creation/rebase time, ``content_yaml_text``
is the branch's editable working copy, and ``content_fingerprint`` gates every
content mutation (compare-and-set). Branch names are unique only among OPEN
branches per tenant, so uniqueness is a conditional insert here rather than a
database unique constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import (
    StatusTransition,
    TransactionContext,
    TransitionResult,
)

OntologyBranchStatus = Literal["open", "merged", "abandoned"]


class OntologyBranchRow(TypedDict):
    id: str
    tenant_id: str
    name: str
    status: str
    base_version_id: str
    base_version_number: int
    base_yaml_text: str
    content_yaml_text: str
    content_fingerprint: str
    created_by: str
    created_at: str
    updated_at: str
    rebased_at: str | None
    merged_proposal_id: str | None
    merged_version_number: int | None


class OntologyBranchActionIdempotencyRow(TypedDict):
    id: str
    tenant_id: str
    actor_user_id: str
    branch_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    response_json: dict[str, object]
    created_at: str


@dataclass(frozen=True)
class OntologyBranchRecord:
    branch_id: str
    tenant_id: str
    name: str
    status: OntologyBranchStatus
    base_version_id: str
    base_version_number: int
    base_yaml_text: str
    content_yaml_text: str
    content_fingerprint: str
    created_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OntologyBranchRebase:
    base_version_id: str
    base_version_number: int
    base_yaml_text: str
    content_yaml_text: str
    content_fingerprint: str
    rebased_at: str
    updated_at: str


@dataclass(frozen=True)
class OntologyBranchActionIdempotencyRecord:
    record_id: str
    tenant_id: str
    actor_user_id: str
    branch_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    response_json: dict[str, object]
    created_at: str


class OntologyBranchRepository(Protocol):
    """DB boundary for tenant-scoped ontology branch working copies."""

    def insert_branch_if_name_free(
        self,
        *,
        transaction: TransactionContext,
        record: OntologyBranchRecord,
    ) -> OntologyBranchRow | None:
        """Insert a branch unless an OPEN branch with the same tenant+name exists.

        Returns the inserted row, or None when the name is already taken by an
        open branch (merged/abandoned branches free their name).
        """
        ...

    def branch_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
    ) -> OntologyBranchRow | None:
        """Return one branch in a tenant."""
        ...

    def open_branch_by_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        name: str,
    ) -> OntologyBranchRow | None:
        """Return the OPEN branch holding this name, if any."""
        ...

    def list_branches(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        status: str | None,
        created_before: str | None,
        before_id: str | None,
        limit: int,
    ) -> list[OntologyBranchRow]:
        """Return branches newest-first with keyset pagination."""
        ...

    def update_branch_content(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        content_yaml_text: str,
        content_fingerprint: str,
        updated_at: str,
    ) -> OntologyBranchRow | None:
        """CAS the working copy: only while open and the fingerprint still matches."""
        ...

    def rebase_branch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        rebase: OntologyBranchRebase,
    ) -> OntologyBranchRow | None:
        """CAS base+content to the rebased snapshot: only while open and fingerprint-matched."""
        ...

    def set_branch_proposal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        proposal_id: str,
        updated_at: str,
    ) -> OntologyBranchRow | None:
        """Record the submitted proposal id on a still-open branch."""
        ...

    def transition_branch_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        branch_id: str,
        transition: StatusTransition,
        merged_version_number: int | None,
        updated_at: str,
    ) -> TransitionResult:
        """CAS open -> merged/abandoned, recording the merged version when merging."""
        ...

    def action_idempotency_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        actor_user_id: str,
        branch_id: str,
        operation: str,
        idempotency_key: str,
    ) -> OntologyBranchActionIdempotencyRow | None:
        """Return the durable result bound to one branch Action mutation key."""
        ...

    def insert_action_idempotency_or_existing(
        self,
        *,
        transaction: TransactionContext,
        record: OntologyBranchActionIdempotencyRecord,
    ) -> OntologyBranchActionIdempotencyRow | None:
        """Insert the record, returning the existing winner on unique conflict."""
        ...
