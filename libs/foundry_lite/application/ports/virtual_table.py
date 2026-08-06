"""Application port contract for virtual tables (registration + push-down reads).

A virtual table is a POINTER to a table in an external system, not a copy of it. Palantir's
contract is explicit: register a source "without having to create redundant copies of the
associated data or pipelining logic". The moment rows land in our storage this stops being a
virtual table and becomes an ingest, so nothing in this port returns a materialized dataset.

Two things follow from that and are encoded here rather than left to each adapter:

* **The schema is pinned at registration.** A pointer whose shape can change under it is not a
  contract. If the source's columns drift, that is a failure to surface, not a silent follow.
* **A read reports where each predicate ran.** Palantir pushes what the source supports down to
  the source and executes the rest locally. Both are legitimate, but a caller that cannot tell
  them apart cannot reason about cost or about how much data crossed the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


class VirtualTableAlreadyExistsError(Exception):
    """Raised when a pointer with this name is already registered in the folder.

    A port-level error, not a domain one: the repository reports what storage refused, and the
    service decides what that means for the caller.
    """


def _empty_mapping() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class VirtualTableColumn:
    """One pinned column of a registered virtual table."""

    name: str
    data_type: str
    is_nullable: bool = True


@dataclass(frozen=True)
class VirtualTableSchema:
    """The column shape pinned when the table was registered.

    Drift against this is reported, never absorbed: a downstream object type or pipeline node
    was built against these columns.
    """

    columns: tuple[VirtualTableColumn, ...] = ()

    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


@dataclass(frozen=True)
class VirtualTableRecord:
    """A registered pointer to one external table.

    Field names mirror Palantir's Create Virtual Table resource (``rid``, ``name``,
    ``parentRid``, ``config``, ``markings``) so the two contracts stay readable side by side.
    ``config`` is a mapping rather than a flat column pair because the resource models it as a
    union — a SQL source identifies a table by schema+name, object storage by path+format, and
    flattening those loses the distinction.
    """

    rid: str
    tenant_id: str
    name: str
    parent_rid: str
    connection_rid: str
    config: Mapping[str, object]
    schema: VirtualTableSchema
    markings: tuple[str, ...] = ()
    created_at: str = ""


@dataclass(frozen=True)
class VirtualTablePredicate:
    """One column comparison a caller wants applied before rows leave the source."""

    column: str
    operator: str
    value: object


@dataclass(frozen=True)
class VirtualTableQuery:
    """A bounded read against a registered virtual table.

    ``limit`` is required rather than optional: the whole point of the pointer is that the
    remote table may be far larger than anything we should pull, so an unbounded read is not a
    query this port offers.
    """

    predicates: tuple[VirtualTablePredicate, ...] = ()
    projection: tuple[str, ...] = ()
    limit: int = 100


@dataclass(frozen=True)
class VirtualTableReadResult:
    """Rows plus the evidence of where the work actually happened."""

    rows: tuple[Mapping[str, object], ...]
    # Predicates the adapter translated into the source's own query language. These filtered
    # rows before they crossed the network.
    pushed_down_predicates: tuple[VirtualTablePredicate, ...] = ()
    # Predicates the source could not express, applied locally after fetching. Reported rather
    # than hidden so a caller can see when a read silently became a wider scan.
    local_predicates: tuple[VirtualTablePredicate, ...] = ()
    network_evidence: Mapping[str, object] = field(default_factory=_empty_mapping)

    def has_full_push_down(self) -> bool:
        return not self.local_predicates


class VirtualTableRepository(Protocol):
    """Durable registry of virtual-table pointers. Stores metadata only, never rows."""

    def register(self, record: VirtualTableRecord) -> VirtualTableRecord:
        """Persist one pointer. A duplicate (tenant, parent, name) is a conflict."""
        ...

    def get(self, *, tenant_id: str, rid: str) -> VirtualTableRecord | None:
        """Return one tenant-scoped pointer by rid."""
        ...

    def list_for_connection(self, *, tenant_id: str, connection_rid: str) -> tuple[VirtualTableRecord, ...]:
        """Return every tenant-scoped pointer registered against one connection."""
        ...

    def delete(self, *, tenant_id: str, rid: str) -> None:
        """Remove one pointer. The external table is untouched."""
        ...


@dataclass(frozen=True)
class ExternalTableRef:
    """One table the source says exists and the configured credential can reach.

    Discovery is credential-scoped on purpose: Palantir registers "all tables in the source that
    are accessible to the configured credentials", so what the platform can see is exactly what
    the connection can see, and nothing about a table nobody is allowed to read leaks into a
    Foundry-side listing.
    """

    schema_name: str
    table_name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class VirtualTableReader(Protocol):
    """Boundary that executes a bounded read against the external table itself."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def describe(self, *, connection_url: str, config: Mapping[str, object]) -> VirtualTableSchema:
        """Read the external table's current column shape, for pinning or drift comparison."""
        ...

    def discover(self, *, connection_url: str, schema_names: tuple[str, ...] = ()) -> tuple[ExternalTableRef, ...]:
        """List the tables this credential can reach, optionally narrowed to given schemas."""
        ...

    def read(
        self,
        *,
        connection_url: str,
        config: Mapping[str, object],
        query: VirtualTableQuery,
    ) -> VirtualTableReadResult:
        """Execute a bounded read, pushing down what the source can express."""
        ...


def schema_drift(pinned: VirtualTableSchema, observed: VirtualTableSchema) -> tuple[str, ...]:
    """Describe how the source diverged from what was pinned at registration.

    Returned as messages rather than a bool so an operator sees which column moved. An empty
    tuple means the pointer still matches its contract.
    """
    findings: list[str] = []
    pinned_by_name = {column.name: column for column in pinned.columns}
    observed_by_name = {column.name: column for column in observed.columns}
    for name in sorted(set(pinned_by_name) - set(observed_by_name)):
        findings.append(f"column removed at source: {name}")
    for name in sorted(set(pinned_by_name) & set(observed_by_name)):
        was, now = pinned_by_name[name], observed_by_name[name]
        if was.data_type != now.data_type:
            findings.append(f"column type changed at source: {name} {was.data_type} -> {now.data_type}")
    return tuple(findings)


def projected_columns(schema: VirtualTableSchema, projection: Sequence[str]) -> tuple[str, ...]:
    """Resolve a projection against the pinned schema, rejecting unknown columns.

    Resolving against the pin rather than against the live table is deliberate: a column that
    appeared at the source after registration is not part of this pointer's contract.
    """
    if not projection:
        return schema.column_names()
    known = set(schema.column_names())
    unknown = sorted(name for name in projection if name not in known)
    if unknown:
        raise ValueError(f"projection references columns absent from the pinned schema: {unknown}")
    return tuple(projection)
