"""Registration and inspection of virtual tables.

A virtual table is a pointer, so registering one is a governance act rather than a data
movement: it decides that a table in someone else's system is now visible inside a project,
under a set of markings, to whoever can read that project. The registry alone could not make
that decision -- it has no policy, no tenant boundary, and no audit trail -- so those live here.

Registration pins the schema by asking the source for it. Palantir's contract is that a virtual
table is registered "without having to create redundant copies", which means the shape is the
only thing we hold, and a shape nobody verified is a promise nobody checked. `describe` also
proves the connection works before a pointer exists that claims it does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.virtual_table import (
    ExternalTableRef,
    VirtualTableAlreadyExistsError,
    VirtualTableReader,
    VirtualTableRecord,
    VirtualTableRepository,
    VirtualTableSchema,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.action_helpers import SupportsAudit
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed
from foundry_lite.security.policy import PolicyService

# The name of the config field, not a credential: it holds a vault path the reader
# resolves at read time. Named for what it is so a scanner is not the only reader misled.
_CONNECTION_REFERENCE_FIELD = "databaseUrlSecretRef"


@dataclass(frozen=True)
class BulkRegistrationFailure:
    """One table that could not be registered, named so the caller need not diff the folder."""

    table: str
    reason: str


@dataclass(frozen=True)
class AutoRegistrationPlan:
    """What a scheduled pass found: what is new, and what the source no longer has."""

    new_tables: tuple[ExternalTableRef, ...]
    missing_tables: tuple[str, ...]


def _registered_qualified_name(record: VirtualTableRecord) -> str:
    return f"{record.config.get('schema')}.{record.config.get('table')}"


@dataclass(frozen=True)
class BulkRegistrationResult:
    registered: tuple[VirtualTableRecord, ...]
    failures: tuple[BulkRegistrationFailure, ...]


def _child_folder_rid(parent_rid: str, schema_name: str) -> str:
    """Mirror the source's own hierarchy, the way auto-registration does in Palantir.

    A flat folder of four hundred pointers is unusable, and the source already has a structure
    worth keeping: its schema is the grouping the people who built it chose.
    """
    return f"{parent_rid}/{schema_name}"


class VirtualTableService(CoreService):
    """Register, list, inspect, and remove pointers to external tables."""

    required_dependencies = ("engine", "policy", "virtual_table_repository", "virtual_table_reader", "secret_vault")
    required_collaborators = ("runtime_service",)
    policy: PolicyService
    virtual_table_repository: VirtualTableRepository
    virtual_table_reader: VirtualTableReader
    runtime_service: SupportsAudit

    def register_virtual_table(
        self,
        *,
        name: str,
        parent_rid: str,
        connection_rid: str,
        config: Mapping[str, object],
        markings: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> VirtualTableRecord:
        """Pin the source's current shape and record the pointer."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:write")
        _require_secret_reference(config)
        schema = self._described_schema(ctx, config)
        record = VirtualTableRecord(
            rid=_new_id("vt"),
            tenant_id=ctx.tenant_id,
            name=name,
            parent_rid=parent_rid,
            connection_rid=connection_rid,
            config=dict(config),
            schema=schema,
            markings=tuple(markings),
        )
        try:
            registered = self.virtual_table_repository.register(record)
        except VirtualTableAlreadyExistsError as exc:
            raise ConflictDetected(
                "a virtual table with this name is already registered in the folder",
                details={"name": name, "parentRid": parent_rid},
            ) from exc
        self._audit(ctx, registered, action="register")
        return registered

    def discover_external_tables(
        self, *, config: Mapping[str, object], schema_names: tuple[str, ...] = (), ctx: RequestContext | None = None
    ) -> tuple[ExternalTableRef, ...]:
        """List what the connection can reach, so a caller can choose before registering."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        _require_secret_reference(config)
        connection_url = self.secret_vault.get_secret(str(config[_CONNECTION_REFERENCE_FIELD])).value
        return self.virtual_table_reader.discover(connection_url=connection_url, schema_names=schema_names)

    def register_virtual_tables(
        self,
        *,
        parent_rid: str,
        connection_rid: str,
        config: Mapping[str, object],
        tables: Sequence[ExternalTableRef],
        markings: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> BulkRegistrationResult:
        """Register many pointers at once, reporting per-table outcomes.

        One table failing does not abandon the rest. Bulk registration is a picker action over
        tables a caller already saw, and a source that renamed one of them between listing and
        registering should not cost the other forty. Every failure is named and returned, so the
        caller sees which pointers exist afterwards rather than having to diff the folder.
        """
        ctx = ctx or RequestContext()
        registered: list[VirtualTableRecord] = []
        failures: list[BulkRegistrationFailure] = []
        for table in tables:
            try:
                registered.append(
                    self.register_virtual_table(
                        name=table.table_name,
                        parent_rid=_child_folder_rid(parent_rid, table.schema_name),
                        connection_rid=connection_rid,
                        config={**config, "schema": table.schema_name, "table": table.table_name},
                        markings=markings,
                        ctx=ctx,
                    )
                )
            except (FoundryLiteError, AdapterError) as exc:
                failures.append(BulkRegistrationFailure(table=table.qualified_name, reason=type(exc).__name__))
        return BulkRegistrationResult(registered=tuple(registered), failures=tuple(failures))

    def preview_auto_registration(
        self,
        *,
        connection_rid: str,
        config: Mapping[str, object],
        schema_names: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> AutoRegistrationPlan:
        """What a scheduled pass would change, without changing it.

        Missing tables are reported and never unregistered. Palantir does the same, and the
        reason is that the platform cannot tell a dropped table from a source that is briefly
        unreachable or mid-migration. Removing the pointer would break every pipeline and object
        type built on it; leaving it fails at read time, which is recoverable and legible.
        """
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        available = self.discover_external_tables(config=config, schema_names=schema_names, ctx=ctx)
        registered = self.list_virtual_tables(connection_rid=connection_rid, ctx=ctx)
        known = {_registered_qualified_name(record) for record in registered}
        present = {ref.qualified_name for ref in available}
        return AutoRegistrationPlan(
            new_tables=tuple(ref for ref in available if ref.qualified_name not in known),
            missing_tables=tuple(sorted(known - present)),
        )

    def run_auto_registration(
        self,
        *,
        parent_rid: str,
        connection_rid: str,
        config: Mapping[str, object],
        schema_names: tuple[str, ...] = (),
        markings: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> BulkRegistrationResult:
        """Register whatever appeared at the source since the last pass.

        Diffs before registering rather than relying on the duplicate conflict, so a scheduled
        pass over a stable source reports nothing instead of one conflict per existing table.
        """
        ctx = ctx or RequestContext()
        plan = self.preview_auto_registration(
            connection_rid=connection_rid, config=config, schema_names=schema_names, ctx=ctx
        )
        return self.register_virtual_tables(
            parent_rid=parent_rid,
            connection_rid=connection_rid,
            config=config,
            tables=plan.new_tables,
            markings=markings,
            ctx=ctx,
        )

    def list_virtual_tables(
        self, *, connection_rid: str, ctx: RequestContext | None = None
    ) -> tuple[VirtualTableRecord, ...]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        return self.virtual_table_repository.list_for_connection(tenant_id=ctx.tenant_id, connection_rid=connection_rid)

    def get_virtual_table(self, rid: str, *, ctx: RequestContext | None = None) -> VirtualTableRecord:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        return self._required_record(ctx, rid)

    def delete_virtual_table(self, rid: str, *, ctx: RequestContext | None = None) -> None:
        """Remove the pointer. The external table is untouched -- we never owned it."""
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:write")
        record = self._required_record(ctx, rid)
        self.virtual_table_repository.delete(tenant_id=ctx.tenant_id, rid=rid)
        self._audit(ctx, record, action="delete")

    def inspect_schema_drift(self, rid: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        """Compare the pinned schema against the source as it is now.

        Reported, never absorbed. A pipeline node or object type was built against the pinned
        columns, so silently adopting the source's new shape would change what those consumers
        resolve to without anyone approving it.
        """
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        record = self._required_record(ctx, rid)
        observed = self._described_schema(ctx, record.config)
        pinned_names = set(record.schema.column_names())
        observed_names = set(observed.column_names())
        return {
            "virtualTableRid": record.rid,
            "hasDrifted": record.schema.columns != observed.columns,
            "addedColumns": sorted(observed_names - pinned_names),
            "removedColumns": sorted(pinned_names - observed_names),
            "pinnedColumnCount": len(record.schema.columns),
            "observedColumnCount": len(observed.columns),
        }

    def _described_schema(self, ctx: RequestContext, config: Mapping[str, object]) -> VirtualTableSchema:
        del ctx
        connection_url = self.secret_vault.get_secret(str(config[_CONNECTION_REFERENCE_FIELD])).value
        return self.virtual_table_reader.describe(connection_url=connection_url, config=config)

    def _required_record(self, ctx: RequestContext, rid: str) -> VirtualTableRecord:
        record = self.virtual_table_repository.get(tenant_id=ctx.tenant_id, rid=rid)
        if record is None:
            raise NotFound("virtual table not found", details={"virtualTableRid": rid})
        return record

    def _audit(self, ctx: RequestContext, record: VirtualTableRecord, *, action: str) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type=f"virtual_table.{action}",
                resource_type="virtual_table",
                resource_id=record.rid,
                action=action,
                decision="allow",
                # The connection URL is never here: `config` carries a vault reference, and the
                # audit trail records which pointer was created, not how to reach behind it.
                after_ref={"name": record.name, "parentRid": record.parent_rid, "markings": list(record.markings)},
            )


def _require_secret_reference(config: Mapping[str, object]) -> None:
    """A pointer stores a reference to a credential, never the credential.

    Accepting a URL here would put a password in the registry, in every audit payload that
    echoes the config, and in every API response that returns the pointer.
    """
    reference = config.get(_CONNECTION_REFERENCE_FIELD)
    if not isinstance(reference, str) or not reference.strip():
        raise ValidationFailed(
            "virtual table config must reference the connection secret by name",
            details={"requiredKey": _CONNECTION_REFERENCE_FIELD},
        )
    if "://" in reference or "@" in reference:
        raise ValidationFailed(
            "virtual table config must hold a secret reference, not a connection URL",
            details={"requiredKey": _CONNECTION_REFERENCE_FIELD},
        )
