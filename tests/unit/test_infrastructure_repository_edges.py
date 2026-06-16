from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from foundry_lite.application.ports.action_repository import ActionRunRecord
from foundry_lite.infrastructure.local_runtime import (
    _compute_adapter,
    _connector_adapter,
    _dataset_storage_adapter,
    _search_adapter,
    _stream_adapter,
    _workflow_adapter,
)
from foundry_lite.infrastructure.repositories.action_repository import SqlAlchemyActionRepository
from foundry_lite.infrastructure.repositories.dataset_transaction_repository import _webhook_event_matches
from foundry_lite.infrastructure.repositories.object_change_sequence import next_object_change_sequence


@dataclass
class _FakeResult:
    scalar: str | None = None
    row: tuple[int] | None = None

    def scalar_one_or_none(self) -> str | None:
        return self.scalar

    def first(self) -> tuple[int] | None:
        return self.row


class _FakeTransaction:
    def __init__(self, dialect_name: str, result: _FakeResult | None = None) -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.result = result or _FakeResult()

    def execute(self, statement: object) -> _FakeResult:
        del statement
        return self.result


def test_runtime_adapter_factories_fail_closed_for_unknown_profiles(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _dataset_storage_adapter("typo-profile", tmp_path)
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _compute_adapter("typo-profile")
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _connector_adapter("typo-profile")
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _stream_adapter("typo-profile")
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _workflow_adapter("typo-profile")


def test_runtime_search_adapter_selects_elasticsearch_and_rejects_unknown_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_SEARCH_PROFILE", "elasticsearch")
    adapter = _search_adapter("local")
    assert adapter.profile_name == "elasticsearch"

    monkeypatch.setenv("FOUNDRY_LITE_SEARCH_PROFILE", "missing-search")
    with pytest.raises(ValueError, match="unknown search adapter profile"):
        _search_adapter("local")


def test_object_change_sequence_rejects_unknown_dialect() -> None:
    with pytest.raises(RuntimeError, match="unsupported object change sequence dialect"):
        next_object_change_sequence(_FakeTransaction("unknown"), "tenant-demo")


def test_object_change_sequence_requires_returned_counter_row() -> None:
    transaction = _FakeTransaction("sqlite", _FakeResult(row=None))

    with pytest.raises(RuntimeError, match="allocation returned no row"):
        next_object_change_sequence(transaction, "tenant-demo")


def test_action_idempotency_insert_requires_persisted_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = SqlAlchemyActionRepository(None)  # type: ignore[arg-type]
    monkeypatch.setattr(repository, "action_run_by_idempotency", lambda **_: None)
    transaction = _FakeTransaction("generic", _FakeResult(scalar="different-run"))

    with pytest.raises(RuntimeError, match="no persisted winner"):
        repository.insert_action_run_or_get_existing(transaction=transaction, record=_action_run_record())


def test_webhook_event_matcher_rejects_malformed_metadata() -> None:
    assert _webhook_event_matches(None, "mock_saas", "orders", "evt-1") is False
    assert _webhook_event_matches({"webhookEvent": "evt-1"}, "mock_saas", "orders", "evt-1") is False


def _action_run_record() -> ActionRunRecord:
    return ActionRunRecord(
        action_run_id="arun_loser",
        tenant_id="tenant-demo",
        action_type_id="atype_approve",
        action_type_api_name="approveOrder",
        actor_user_id="actor-demo",
        target_object_type_id="ot_order",
        target_object_type_api_name="Order",
        target_object_id="O-1",
        expected_object_version=1,
        parameters={"status": "APPROVED"},
        status="received",
        idempotency_key="idem-1",
        request_fingerprint="fingerprint-1",
        error=None,
        created_at="2026-06-10T00:00:00Z",
        completed_at=None,
    )
