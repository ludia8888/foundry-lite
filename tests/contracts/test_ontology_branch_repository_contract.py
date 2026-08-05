"""Contract for ``OntologyBranchRepository`` (true ontology branching storage).

Runs against a deterministic fake, SQLite (fast inner loop), and PostgreSQL
(production parity). Locks the swap-point behaviors the branch service relies
on: open-name-conditional insert (a merged/abandoned branch frees its name),
fingerprint-CAS content updates and rebases that only land while the branch is
still open, CAS status transitions (open -> merged records the merged version
number exactly once), tenant scoping, and keyset list pagination.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports.ontology_branch_repository import (
    OntologyBranchActionIdempotencyRecord,
    OntologyBranchActionIdempotencyRow,
    OntologyBranchRebase,
    OntologyBranchRecord,
    OntologyBranchRepository,
    OntologyBranchRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition, TransitionResult
from foundry_lite.application.state_transitions import (
    ONTOLOGY_BRANCH_ABANDONED,
    ONTOLOGY_BRANCH_MERGED,
    classify_transition_miss,
    transition_updated,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyOntologyBranchRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from tests.contracts.conftest import PostgresFixture, skip_if_no_postgres

_TENANT = "tenant-test"
_OTHER_TENANT = "tenant-demo"


class OntologyBranchHarness(Protocol):
    @property
    def repository(self) -> OntologyBranchRepository: ...

    def transaction(self) -> AbstractContextManager[Any]: ...


@dataclass
class FakeOntologyBranchRepository:
    rows: dict[str, OntologyBranchRow] = field(default_factory=dict)
    action_idempotency_rows: dict[tuple[str, str, str, str, str], OntologyBranchActionIdempotencyRow] = field(
        default_factory=dict
    )

    def insert_branch_if_name_free(self, *, transaction: Any, record: OntologyBranchRecord) -> OntologyBranchRow | None:
        del transaction
        if self.open_branch_by_name(transaction=None, tenant_id=record.tenant_id, name=record.name) is not None:
            return None
        row = cast(
            OntologyBranchRow,
            {
                "id": record.branch_id,
                "tenant_id": record.tenant_id,
                "name": record.name,
                "status": record.status,
                "base_version_id": record.base_version_id,
                "base_version_number": record.base_version_number,
                "base_yaml_text": record.base_yaml_text,
                "content_yaml_text": record.content_yaml_text,
                "content_fingerprint": record.content_fingerprint,
                "created_by": record.created_by,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "rebased_at": None,
                "merged_proposal_id": None,
                "merged_version_number": None,
            },
        )
        self.rows[record.branch_id] = row
        return cast(OntologyBranchRow, dict(row))

    def branch_by_id(self, *, transaction: Any, tenant_id: str, branch_id: str) -> OntologyBranchRow | None:
        del transaction
        row = self.rows.get(branch_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return cast(OntologyBranchRow, dict(row))

    def open_branch_by_name(self, *, transaction: Any, tenant_id: str, name: str) -> OntologyBranchRow | None:
        del transaction
        for row in self.rows.values():
            if row["tenant_id"] == tenant_id and row["name"] == name and row["status"] == "open":
                return cast(OntologyBranchRow, dict(row))
        return None

    def list_branches(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        status: str | None,
        created_before: str | None,
        before_id: str | None,
        limit: int,
    ) -> list[OntologyBranchRow]:
        del transaction
        rows = [row for row in self.rows.values() if row["tenant_id"] == tenant_id]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        if created_before is not None and before_id is not None:
            rows = [
                row
                for row in rows
                if row["created_at"] < created_before or (row["created_at"] == created_before and row["id"] < before_id)
            ]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
        return [cast(OntologyBranchRow, dict(row)) for row in rows[:limit]]

    def update_branch_content(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        content_yaml_text: str,
        content_fingerprint: str,
        updated_at: str,
    ) -> OntologyBranchRow | None:
        del transaction
        row = self.rows.get(branch_id)
        if (
            row is None
            or row["tenant_id"] != tenant_id
            or row["status"] != "open"
            or row["content_fingerprint"] != expected_fingerprint
        ):
            return None
        mutable = cast(dict[str, Any], row)
        mutable.update(
            content_yaml_text=content_yaml_text, content_fingerprint=content_fingerprint, updated_at=updated_at
        )
        return cast(OntologyBranchRow, dict(row))

    def rebase_branch(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        rebase: OntologyBranchRebase,
    ) -> OntologyBranchRow | None:
        del transaction
        row = self.rows.get(branch_id)
        if (
            row is None
            or row["tenant_id"] != tenant_id
            or row["status"] != "open"
            or row["content_fingerprint"] != expected_fingerprint
        ):
            return None
        mutable = cast(dict[str, Any], row)
        mutable.update(
            base_version_id=rebase.base_version_id,
            base_version_number=rebase.base_version_number,
            base_yaml_text=rebase.base_yaml_text,
            content_yaml_text=rebase.content_yaml_text,
            content_fingerprint=rebase.content_fingerprint,
            rebased_at=rebase.rebased_at,
            updated_at=rebase.updated_at,
        )
        return cast(OntologyBranchRow, dict(row))

    def set_branch_proposal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        proposal_id: str,
        updated_at: str,
    ) -> OntologyBranchRow | None:
        del transaction
        row = self.rows.get(branch_id)
        if row is None or row["tenant_id"] != tenant_id or row["status"] != "open":
            return None
        cast(dict[str, Any], row).update(merged_proposal_id=proposal_id, updated_at=updated_at)
        return cast(OntologyBranchRow, dict(row))

    def transition_branch_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        transition: StatusTransition,
        merged_version_number: int | None,
        updated_at: str,
    ) -> TransitionResult:
        del transaction
        row = self.rows.get(branch_id)
        if row is None or row["tenant_id"] != tenant_id:
            return classify_transition_miss(transition, None)
        if row["status"] not in transition.from_statuses:
            return classify_transition_miss(transition, row["status"])
        mutable = cast(dict[str, Any], row)
        mutable.update(status=transition.to_status, updated_at=updated_at)
        if merged_version_number is not None:
            mutable.update(merged_version_number=merged_version_number)
        return transition_updated(transition)

    def action_idempotency_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        actor_user_id: str,
        branch_id: str,
        operation: str,
        idempotency_key: str,
    ) -> OntologyBranchActionIdempotencyRow | None:
        del transaction
        row = self.action_idempotency_rows.get((tenant_id, actor_user_id, branch_id, operation, idempotency_key))
        return cast(OntologyBranchActionIdempotencyRow, dict(row)) if row is not None else None

    def insert_action_idempotency_or_existing(
        self,
        *,
        transaction: Any,
        record: OntologyBranchActionIdempotencyRecord,
    ) -> OntologyBranchActionIdempotencyRow | None:
        del transaction
        key = (
            record.tenant_id,
            record.actor_user_id,
            record.branch_id,
            record.operation,
            record.idempotency_key,
        )
        existing = self.action_idempotency_rows.get(key)
        if existing is not None:
            return cast(OntologyBranchActionIdempotencyRow, dict(existing))
        self.action_idempotency_rows[key] = _idempotency_row(record)
        return None


@dataclass
class FakeOntologyBranchHarness:
    repository: FakeOntologyBranchRepository = field(default_factory=FakeOntologyBranchRepository)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield self


@dataclass
class SqlAlchemyOntologyBranchHarness:
    engine: Engine
    repository: SqlAlchemyOntologyBranchRepository

    @classmethod
    def create(cls, engine: Engine) -> SqlAlchemyOntologyBranchHarness:
        return cls(engine=engine, repository=SqlAlchemyOntologyBranchRepository(engine))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn


@pytest.fixture(params=["fake", "sqlite"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> OntologyBranchHarness:
    if request.param == "fake":
        return FakeOntologyBranchHarness()
    engine = create_engine(f"sqlite:///{tmp_path / 'ontology_branches.db'}", future=True)
    db.create_database(engine)
    return SqlAlchemyOntologyBranchHarness.create(engine)


def _record(
    branch_id: str,
    *,
    tenant_id: str = _TENANT,
    name: str = "feature-a",
    created_at: str = "2026-07-03T00:00:00Z",
) -> OntologyBranchRecord:
    return OntologyBranchRecord(
        branch_id=branch_id,
        tenant_id=tenant_id,
        name=name,
        status="open",
        base_version_id="ver-1",
        base_version_number=1,
        base_yaml_text='{"objectTypes": []}',
        content_yaml_text='{"objectTypes": []}',
        content_fingerprint="sha256:base",
        created_by="user-creator",
        created_at=created_at,
        updated_at=created_at,
    )


def _idempotency_record(
    record_id: str,
    *,
    tenant_id: str = _TENANT,
    actor_user_id: str = "user-creator",
    branch_id: str = "br-1",
    operation: str = "created:SetOrderStatus",
    idempotency_key: str = "action-key",
    request_fingerprint: str = "sha256:request",
) -> OntologyBranchActionIdempotencyRecord:
    return OntologyBranchActionIdempotencyRecord(
        record_id=record_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        branch_id=branch_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        response_json={"branch": {"id": branch_id}, "isReplay": False},
        created_at="2026-08-04T00:00:00Z",
    )


def _idempotency_row(record: OntologyBranchActionIdempotencyRecord) -> OntologyBranchActionIdempotencyRow:
    return OntologyBranchActionIdempotencyRow(
        id=record.record_id,
        tenant_id=record.tenant_id,
        actor_user_id=record.actor_user_id,
        branch_id=record.branch_id,
        operation=record.operation,
        idempotency_key=record.idempotency_key,
        request_fingerprint=record.request_fingerprint,
        response_json=record.response_json,
        created_at=record.created_at,
    )


def test_open_name_conditional_insert_frees_name_after_abandon(harness: OntologyBranchHarness) -> None:
    with harness.transaction() as txn:
        inserted = harness.repository.insert_branch_if_name_free(transaction=txn, record=_record("br-1"))
        duplicate = harness.repository.insert_branch_if_name_free(transaction=txn, record=_record("br-2"))
        other_tenant = harness.repository.insert_branch_if_name_free(
            transaction=txn, record=_record("br-3", tenant_id=_OTHER_TENANT)
        )
        abandoned = harness.repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            transition=ONTOLOGY_BRANCH_ABANDONED,
            merged_version_number=None,
            updated_at="2026-07-03T01:00:00Z",
        )
        reused = harness.repository.insert_branch_if_name_free(transaction=txn, record=_record("br-4"))

    assert inserted is not None and inserted["id"] == "br-1"
    assert duplicate is None
    assert other_tenant is not None and other_tenant["tenant_id"] == _OTHER_TENANT
    assert abandoned.updated
    assert reused is not None and reused["id"] == "br-4"


def test_content_update_is_fingerprint_and_open_status_gated(harness: OntologyBranchHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_branch_if_name_free(transaction=txn, record=_record("br-1"))
        updated = harness.repository.update_branch_content(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            expected_fingerprint="sha256:base",
            content_yaml_text='{"objectTypes": [1]}',
            content_fingerprint="sha256:v2",
            updated_at="2026-07-03T01:00:00Z",
        )
        stale = harness.repository.update_branch_content(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            expected_fingerprint="sha256:base",
            content_yaml_text='{"objectTypes": [2]}',
            content_fingerprint="sha256:v3",
            updated_at="2026-07-03T02:00:00Z",
        )
        harness.repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            transition=ONTOLOGY_BRANCH_ABANDONED,
            merged_version_number=None,
            updated_at="2026-07-03T03:00:00Z",
        )
        after_abandon = harness.repository.update_branch_content(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            expected_fingerprint="sha256:v2",
            content_yaml_text='{"objectTypes": [3]}',
            content_fingerprint="sha256:v4",
            updated_at="2026-07-03T04:00:00Z",
        )

    assert updated is not None and updated["content_fingerprint"] == "sha256:v2"
    assert stale is None
    assert after_abandon is None


def test_action_mutation_idempotency_is_actor_branch_and_request_scoped(
    harness: OntologyBranchHarness,
) -> None:
    first_record = _idempotency_record("idem-1")
    with harness.transaction() as txn:
        first = harness.repository.insert_action_idempotency_or_existing(transaction=txn, record=first_record)
        duplicate = harness.repository.insert_action_idempotency_or_existing(
            transaction=txn,
            record=_idempotency_record("idem-2", request_fingerprint="sha256:different"),
        )
        read = harness.repository.action_idempotency_record(
            transaction=txn,
            tenant_id=_TENANT,
            actor_user_id="user-creator",
            branch_id="br-1",
            operation="created:SetOrderStatus",
            idempotency_key="action-key",
        )
        other_actor = harness.repository.insert_action_idempotency_or_existing(
            transaction=txn,
            record=_idempotency_record("idem-3", actor_user_id="user-other"),
        )
        other_branch = harness.repository.insert_action_idempotency_or_existing(
            transaction=txn,
            record=_idempotency_record("idem-4", branch_id="br-2"),
        )

    assert first is None
    assert duplicate is not None and duplicate["id"] == "idem-1"
    assert read is not None and read["request_fingerprint"] == "sha256:request"
    assert other_actor is None
    assert other_branch is None


def test_rebase_swaps_base_and_content_atomically(harness: OntologyBranchHarness) -> None:
    rebase = OntologyBranchRebase(
        base_version_id="ver-2",
        base_version_number=2,
        base_yaml_text='{"objectTypes": ["main"]}',
        content_yaml_text='{"objectTypes": ["merged"]}',
        content_fingerprint="sha256:rebased",
        rebased_at="2026-07-03T01:00:00Z",
        updated_at="2026-07-03T01:00:00Z",
    )
    with harness.transaction() as txn:
        harness.repository.insert_branch_if_name_free(transaction=txn, record=_record("br-1"))
        stale = harness.repository.rebase_branch(
            transaction=txn, tenant_id=_TENANT, branch_id="br-1", expected_fingerprint="sha256:wrong", rebase=rebase
        )
        rebased = harness.repository.rebase_branch(
            transaction=txn, tenant_id=_TENANT, branch_id="br-1", expected_fingerprint="sha256:base", rebase=rebase
        )

    assert stale is None
    assert rebased is not None
    assert rebased["base_version_id"] == "ver-2"
    assert rebased["base_version_number"] == 2
    assert rebased["base_yaml_text"] == '{"objectTypes": ["main"]}'
    assert rebased["content_yaml_text"] == '{"objectTypes": ["merged"]}'
    assert rebased["content_fingerprint"] == "sha256:rebased"
    assert rebased["rebased_at"] == "2026-07-03T01:00:00Z"


def test_merge_transition_records_version_number_exactly_once(harness: OntologyBranchHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_branch_if_name_free(transaction=txn, record=_record("br-1"))
        proposed = harness.repository.set_branch_proposal(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            proposal_id="ontprop-1",
            updated_at="2026-07-03T01:00:00Z",
        )
        first = harness.repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            transition=ONTOLOGY_BRANCH_MERGED,
            merged_version_number=7,
            updated_at="2026-07-03T02:00:00Z",
        )
        second = harness.repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            transition=ONTOLOGY_BRANCH_MERGED,
            merged_version_number=8,
            updated_at="2026-07-03T03:00:00Z",
        )
        proposal_after_merge = harness.repository.set_branch_proposal(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-1",
            proposal_id="ontprop-2",
            updated_at="2026-07-03T04:00:00Z",
        )
        row = harness.repository.branch_by_id(transaction=txn, tenant_id=_TENANT, branch_id="br-1")
        missing = harness.repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-404",
            transition=ONTOLOGY_BRANCH_MERGED,
            merged_version_number=9,
            updated_at="2026-07-03T05:00:00Z",
        )

    assert proposed is not None and proposed["merged_proposal_id"] == "ontprop-1"
    assert first.updated
    assert second.outcome == "already_terminal"
    assert proposal_after_merge is None
    assert row is not None
    assert row["status"] == "merged"
    assert row["merged_version_number"] == 7
    assert row["merged_proposal_id"] == "ontprop-1"
    assert missing.outcome == "not_found"


def test_list_filters_status_and_pages_by_keyset(harness: OntologyBranchHarness) -> None:
    with harness.transaction() as txn:
        for index in (1, 2, 3):
            harness.repository.insert_branch_if_name_free(
                transaction=txn,
                record=_record(f"br-{index}", name=f"feature-{index}", created_at=f"2026-07-03T0{index}:00:00Z"),
            )
        harness.repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-2",
            transition=ONTOLOGY_BRANCH_ABANDONED,
            merged_version_number=None,
            updated_at="2026-07-03T04:00:00Z",
        )
        open_page_one = harness.repository.list_branches(
            transaction=txn, tenant_id=_TENANT, status="open", created_before=None, before_id=None, limit=1
        )
        open_page_two = harness.repository.list_branches(
            transaction=txn,
            tenant_id=_TENANT,
            status="open",
            created_before=open_page_one[-1]["created_at"],
            before_id=open_page_one[-1]["id"],
            limit=1,
        )
        abandoned_rows = harness.repository.list_branches(
            transaction=txn, tenant_id=_TENANT, status="abandoned", created_before=None, before_id=None, limit=10
        )
        other_tenant_rows = harness.repository.list_branches(
            transaction=txn, tenant_id=_OTHER_TENANT, status=None, created_before=None, before_id=None, limit=10
        )

    assert [row["id"] for row in open_page_one] == ["br-3"]
    assert [row["id"] for row in open_page_two] == ["br-1"]
    assert [row["id"] for row in abandoned_rows] == ["br-2"]
    assert other_tenant_rows == []


def _assert_round_trip(engine: Engine) -> None:
    repository = SqlAlchemyOntologyBranchRepository(engine)
    with engine.begin() as txn:
        inserted = repository.insert_branch_if_name_free(transaction=txn, record=_record("br-pg"))
        duplicate = repository.insert_branch_if_name_free(transaction=txn, record=_record("br-pg-2"))
        merged = repository.transition_branch_status(
            transaction=txn,
            tenant_id=_TENANT,
            branch_id="br-pg",
            transition=ONTOLOGY_BRANCH_MERGED,
            merged_version_number=3,
            updated_at="2026-07-03T01:00:00Z",
        )
        row = repository.branch_by_id(transaction=txn, tenant_id=_TENANT, branch_id="br-pg")
        idempotency_inserted = repository.insert_action_idempotency_or_existing(
            transaction=txn,
            record=_idempotency_record("idem-pg", branch_id="br-pg"),
        )
        idempotency_replay = repository.insert_action_idempotency_or_existing(
            transaction=txn,
            record=_idempotency_record("idem-pg-loser", branch_id="br-pg", request_fingerprint="sha256:other"),
        )
    assert inserted is not None
    assert duplicate is None
    assert merged.updated
    assert row is not None
    assert row["status"] == "merged"
    assert row["merged_version_number"] == 3
    assert idempotency_inserted is None
    assert idempotency_replay is not None and idempotency_replay["id"] == "idem-pg"


@skip_if_no_postgres
def test_ontology_branch_round_trip_postgres(postgres_fixture: PostgresFixture) -> None:
    _assert_round_trip(postgres_fixture.engine)


@skip_if_no_postgres
def test_action_idempotency_postgres_concurrent_insert_has_one_winner(
    postgres_fixture: PostgresFixture,
) -> None:
    barrier = Barrier(2)

    def insert(index: int) -> bool:
        repository = SqlAlchemyOntologyBranchRepository(postgres_fixture.engine)
        barrier.wait()
        with postgres_fixture.engine.begin() as txn:
            existing = repository.insert_action_idempotency_or_existing(
                transaction=txn,
                record=_idempotency_record(f"idem-race-{index}", branch_id="br-race"),
            )
        return existing is None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(pool.map(insert, (1, 2)))
    assert winners.count(True) == 1
