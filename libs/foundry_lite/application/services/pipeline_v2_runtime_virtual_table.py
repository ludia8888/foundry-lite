"""Pipeline runtime for ``source.virtual_table`` — a live read, declared as such.

Unlike every other source node, this one reads a system whose state we do not version. Palantir
is explicit that virtual tables "do not benefit from Foundry dataset capabilities such as
dataset versioning or branching", so the honest thing is to carry that through: the node
requires a contract marked ``is_live_source`` and refuses one carrying a version pin, because a
pin here would tell replay a story that is not true.

The push-down evidence from the read travels into the artifact. A run that fell back to local
filtering read more rows than one that did not, and whoever inspects the build later should be
able to see which happened.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports.virtual_table import (
    VirtualTablePredicate,
    VirtualTableQuery,
    VirtualTableReadResult,
    VirtualTableRecord,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
)
from foundry_lite.domain.errors import NotFound, ValidationFailed

_DEFAULT_LIMIT = 1000


class VirtualTableResolver(Protocol):
    """Resolves a plan's ``virtualTableRef`` to its registered pointer and connection."""

    def resolve(self, *, tenant_id: str, rid: str) -> tuple[VirtualTableRecord, str] | None:
        """Return the pointer and the connection URL to read it through."""
        ...


class VirtualTableReadPort(Protocol):
    def read(
        self,
        *,
        connection_url: str,
        config: Mapping[str, object],
        query: VirtualTableQuery,
    ) -> VirtualTableReadResult: ...


class PipelineV2VirtualTableRuntime:
    """Executes ``source.virtual_table`` by reading the external table at run time."""

    def __init__(self, *, tenant_id: str, resolver: VirtualTableResolver, reader: VirtualTableReadPort) -> None:
        self._tenant_id = tenant_id
        self._resolver = resolver
        self._reader = reader

    def source_virtual_table(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact:
        _validate_live_source(node, contract)
        resolved = self._resolver.resolve(tenant_id=self._tenant_id, rid=contract.resource_ref)
        if resolved is None:
            raise NotFound(
                "virtual table pointer is not registered",
                details={"virtualTableRef": contract.resource_ref, "nodeId": node.node_id},
            )
        record, connection_url = resolved
        result = self._read(node, record, connection_url)
        return _artifact(node, record, result)

    def _read(
        self,
        node: PipelineV2RuntimeNode,
        record: VirtualTableRecord,
        connection_url: str,
    ) -> VirtualTableReadResult:
        return self._reader.read(
            connection_url=connection_url,
            config=record.config,
            query=VirtualTableQuery(
                predicates=_plan_predicates(node),
                projection=record.schema.column_names(),
                limit=_plan_limit(node),
            ),
        )


def _artifact(
    node: PipelineV2RuntimeNode,
    record: VirtualTableRecord,
    result: VirtualTableReadResult,
) -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="table",
        artifact_kind="virtual_table",
        plane="table",
        items=tuple(dict(row) for row in result.rows),
        # The pointer's config is not echoed wholesale: it carries a secret reference and is
        # caller-supplied, so only the fields that identify the external table travel into
        # durable build evidence.
        artifact_ref=_artifact_ref(record),
        manifest=_manifest(record, result),
        security_envelope={"markings": list(record.markings)},
        # A live read produces no committed artifact of ours to serve later: the rows belong
        # to the external system and are already stale by the time anyone asks.
        status="LIVE",
        is_serving=False,
    )


def _validate_live_source(node: PipelineV2RuntimeNode, contract: PipelineV2SourceContract) -> None:
    if contract.descriptor_id != "source.virtual_table":
        raise ValidationFailed(
            "virtual table runtime received a contract for a different source",
            details={"nodeId": node.node_id, "descriptorId": contract.descriptor_id},
        )
    if not getattr(contract, "is_live_source", False):
        raise ValidationFailed(
            "a virtual table source must be declared live; the external system owns its own state",
            details={"nodeId": node.node_id},
        )
    if contract.version_pins:
        raise ValidationFailed(
            "a virtual table source cannot carry a version pin",
            details={"nodeId": node.node_id},
        )


def _plan_predicates(node: PipelineV2RuntimeNode) -> tuple[VirtualTablePredicate, ...]:
    raw = node.config.get("predicates")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        VirtualTablePredicate(
            column=str(item["column"]),
            operator=str(item["operator"]),
            value=item.get("value"),
        )
        for item in raw
        if isinstance(item, Mapping) and "column" in item and "operator" in item
    )


def _plan_limit(node: PipelineV2RuntimeNode) -> int:
    raw = node.config.get("limit")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else _DEFAULT_LIMIT


def _artifact_ref(record: VirtualTableRecord) -> dict[str, object]:
    return {
        "virtualTableRid": record.rid,
        "connectionRid": record.connection_rid,
        "schema": record.config.get("schema"),
        "table": record.config.get("table"),
    }


def _manifest(record: VirtualTableRecord, result: VirtualTableReadResult) -> dict[str, object]:
    """Carry the read's push-down story into the build record.

    ``isLiveSource`` is repeated here rather than left to the plan alone: an operator reading a
    single node's evidence should not have to cross-reference the plan to learn that this row
    set came from an unversioned external system.
    """
    return {
        "isLiveSource": True,
        "virtualTableRid": record.rid,
        "pushedDownPredicates": [
            {"column": item.column, "operator": item.operator} for item in result.pushed_down_predicates
        ],
        "localPredicates": [{"column": item.column, "operator": item.operator} for item in result.local_predicates],
        "hasFullPushDown": result.has_full_push_down(),
        "networkEvidence": dict(result.network_evidence),
    }
