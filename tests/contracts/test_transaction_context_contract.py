from __future__ import annotations

from typing import Any

from foundry_lite.application.ports.transaction_context import TransactionContext


class _MinimalTransactionHandle:
    """A no-op object that satisfies the TransactionContext marker contract."""

    def execute(self, *_: Any, **__: Any) -> None:
        return None


def test_transaction_context_protocol_accepts_arbitrary_handle() -> None:
    """The contract is opaque: any handle whose lifecycle is owned by the
    composition root must be acceptable as a TransactionContext.

    This locks in the boundary semantics so future scale adapters (PostgreSQL
    test containers, in-memory fakes, Kafka transactional writers) can each
    provide their own handle type without changing repository signatures.
    """
    handle: TransactionContext = _MinimalTransactionHandle()  # type: ignore[assignment]
    assert isinstance(handle, TransactionContext)


def test_transaction_context_protocol_accepts_none_for_pure_fakes() -> None:
    """Pure in-memory fake repositories often pass None or a sentinel because
    they don't need a real transaction. The Protocol does not preclude this --
    runtime_checkable Protocols match by structural shape only, and the empty
    Protocol matches everything that is an object instance.
    """
    handle: Any = object()
    assert isinstance(handle, TransactionContext)
