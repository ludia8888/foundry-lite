"""Dataset transaction state requirements shared by finalization paths."""

from __future__ import annotations

from foundry_lite.application.ports import (
    DatasetTransactionRepository,
    DatasetTransactionRow,
    TransactionContext,
)
from foundry_lite.domain.errors import ConflictDetected, NotFound


def require_open_transaction(
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    transaction_id: str,
) -> DatasetTransactionRow:
    transaction = repository.transaction_by_id(transaction=conn, transaction_id=transaction_id)
    if transaction is None:
        raise NotFound("dataset transaction not found", details={"transaction_id": transaction_id})
    if transaction["status"] != "OPEN":
        raise ConflictDetected(
            "dataset transaction is not open",
            details={"transaction_id": transaction_id, "status": transaction["status"]},
        )
    return transaction
