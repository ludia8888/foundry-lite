from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TransactionContext(Protocol):
    """Opaque transaction handle passed from application services into repositories.

    Application services own transaction lifecycle (via the composition root's
    engine/begin equivalent) and pass the resulting handle into repository ports.
    Repositories may invoke vendor-specific methods on the handle internally;
    callers must not. This protocol exists so that fake adapters, in-memory
    runtimes, and future scale-out backends (PostgreSQL test containers,
    transactional Kafka outbox writers, etc.) can each provide their own
    transaction handle type while sharing the same port surface.

    The protocol is intentionally empty (a marker contract): the supported
    operations are defined by the concrete repository implementation pairing.
    What it documents is the boundary, not the vendor API.
    """
