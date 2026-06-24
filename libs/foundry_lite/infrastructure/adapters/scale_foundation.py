from __future__ import annotations

import math
from collections.abc import Mapping

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.connector_adapter import (
    ConnectorAdapter,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
)
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.search_adapter import (
    SearchAdapter,
    SearchDocument,
    SearchHit,
    SearchIndexMapping,
    SearchQuery,
)
from foundry_lite.application.ports.stream_adapter import StreamAdapter, StreamEvent, StreamPublishRequest
from foundry_lite.application.ports.workflow_adapter import (
    WorkflowAdapter,
    WorkflowRun,
    WorkflowStartRequest,
    workflow_run_id,
    workflow_run_id_matches_tenant,
)


class LocalWorkflowAdapter:
    """In-memory workflow boundary for local/demo execution."""

    profile_name = "local-workflow"

    def __init__(self) -> None:
        self._runs_by_id: dict[str, WorkflowRun] = {}
        self._runs_by_idempotency: dict[str, WorkflowRun] = {}
        self._run_tenants: dict[str, str] = {}

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "start_workflow",
                    "timeout",
                    True,
                    "Workflow start timed out; retry with the same idempotency key.",
                    timeout_seconds=60,
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode("workflow_run", "not_found", False, "Workflow run was not found."),
            ),
        )

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        run_id = workflow_run_id(request)
        existing = self._runs_by_idempotency.get(run_id)
        if existing is not None:
            return existing
        run = WorkflowRun(
            run_id=run_id,
            workflow_name=request.workflow_name,
            status="succeeded",
            output={
                "processed": True,
                **dict(request.input),
                "workflow_name": request.workflow_name,
                "request_id": request.request_id,
            },
        )
        self._runs_by_id[run.run_id] = run
        self._runs_by_idempotency[run_id] = run
        self._run_tenants[run.run_id] = request.tenant_id
        return run

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        if not workflow_run_id_matches_tenant(tenant_id, run_id):
            return None
        if self._run_tenants.get(run_id) != tenant_id:
            return None
        return self._runs_by_id.get(run_id)


class FakeWorkflowAdapter(LocalWorkflowAdapter):
    """Fake workflow profile that preserves the local contract surface."""

    profile_name = "fake-workflow"


class LocalStreamAdapter:
    """In-memory stream boundary for local/demo execution."""

    profile_name = "local-stream"

    def __init__(self) -> None:
        self._events_by_stream: dict[str, list[StreamEvent]] = {}

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "publish_event",
                    "unavailable",
                    True,
                    "Stream publish failed; retry with the same event key and payload.",
                ),
                AdapterFailureMode(
                    "read_events",
                    "timeout",
                    True,
                    "Stream read timed out; retry from the same offset checkpoint.",
                    timeout_seconds=30,
                ),
            ),
        )

    def publish_event(self, request: StreamPublishRequest) -> StreamEvent:
        events = self._events_by_stream.setdefault(request.stream_name, [])
        event = StreamEvent(
            stream_name=request.stream_name,
            offset=len(events),
            event_type=request.event_type,
            tenant_id=request.tenant_id,
            request_id=request.request_id,
            key=request.key,
            payload=dict(request.payload),
        )
        events.append(event)
        return event

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        events = self._events_by_stream.get(stream_name, [])
        start = -1 if after_offset is None else after_offset
        return [event for event in events if event.offset > start][:limit]


class FakeStreamAdapter(LocalStreamAdapter):
    """Fake stream profile that preserves the local contract surface."""

    profile_name = "fake-stream"


class LocalSearchAdapter:
    """In-memory exact-match search boundary for local/demo execution."""

    profile_name = "local-search"

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str, str], SearchDocument] = {}
        self._mappings: dict[tuple[str, str], SearchIndexMapping] = {}

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("configure_index", "unavailable", True, "Search index mapping is unavailable."),
                AdapterFailureMode("upsert_document", "unavailable", True, "Search index write is unavailable."),
                AdapterFailureMode("delete_document", "unavailable", True, "Search index delete is unavailable."),
                AdapterFailureMode(
                    "document_ids",
                    "unavailable",
                    True,
                    "Search index consistency read is unavailable.",
                ),
                AdapterFailureMode(
                    "search",
                    "timeout",
                    True,
                    "Search query timed out; retry without changing object-store state.",
                    timeout_seconds=10,
                ),
            ),
        )

    def upsert_document(self, document: SearchDocument) -> None:
        current = self._documents.get((document.tenant_id, document.object_type, document.document_id))
        if current is not None and document.version <= current.version:
            return
        self._documents[(document.tenant_id, document.object_type, document.document_id)] = document

    def configure_index(self, mapping: SearchIndexMapping) -> None:
        self._mappings[(mapping.tenant_id, mapping.object_type)] = mapping

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        self._documents.pop((tenant_id, object_type, document_id), None)

    def document_ids(self, *, tenant_id: str, object_type: str) -> list[str]:
        return sorted(
            document_id
            for doc_tenant, doc_type, document_id in self._documents
            if doc_tenant == tenant_id and doc_type == object_type
        )

    def search(self, query: SearchQuery) -> list[SearchHit]:
        scoped = [
            document
            for key, document in sorted(self._documents.items())
            if key[0] == query.tenant_id and key[1] == query.object_type and _matches_terms(document, query)
        ]
        if query.query_vector is not None:
            return self._nearest_neighbors(scoped, query)
        mapping = self._mappings.get((query.tenant_id, query.object_type))
        matches = [
            SearchHit(document_id=document.document_id, score=1.0, document=document)
            for document in scoped
            if _matches_full_text(document, query, mapping)
        ]
        return matches[: query.limit]

    def _nearest_neighbors(self, scoped: list[SearchDocument], query: SearchQuery) -> list[SearchHit]:
        """Cosine kNN over the model-pinned object vectors (Palantir nearestNeighbors)."""
        embedded = [document for document in scoped if document.embedding]
        _guard_search_model_version(self.profile_name, embedded, query.embedding_model_version)
        scored = [(_cosine(document.embedding, query.query_vector or ()), document) for document in embedded]
        ranked = [(score, document) for score, document in scored if score > 0.0]
        ranked.sort(key=lambda pair: (-pair[0], pair[1].document_id))
        return [
            SearchHit(document_id=document.document_id, score=score, document=document) for score, document in ranked
        ][: query.limit]


def _matches_terms(document: SearchDocument, query: SearchQuery) -> bool:
    return all(document.properties.get(name) == value for name, value in query.terms.items())


def _guard_search_model_version(profile: str, documents: list[SearchDocument], query_model_version: str) -> None:
    """Fail closed when a query vector's model differs from an indexed object vector's."""
    for document in documents:
        if document.embedding_model_version != query_model_version:
            raise AdapterError(
                AdapterFailure(
                    profile,
                    "search",
                    "conflict",
                    False,
                    "object search embedding model mismatch "
                    f"(index {document.embedding_model_version!r}, query {query_model_version!r})",
                )
            )


def _cosine(a: EmbeddingVector, b: EmbeddingVector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _matches_full_text(
    document: SearchDocument,
    query: SearchQuery,
    mapping: SearchIndexMapping | None,
) -> bool:
    if query.text is None:
        return True
    fields = query.searchable_properties or (mapping.searchable_properties if mapping else tuple(document.properties))
    needle = query.text.casefold()
    return any(needle in str(document.properties.get(field, "")).casefold() for field in fields)


class FakeSearchAdapter(LocalSearchAdapter):
    """Fake search profile that preserves the local contract surface."""

    profile_name = "fake-search"


ConnectorSnapshotKey = tuple[str, str, str]


class LocalConnectorAdapter:
    """Configured in-memory connector boundary for local/demo execution."""

    profile_name = "local-connector"

    def __init__(self, snapshots: Mapping[ConnectorSnapshotKey, ConnectorSnapshot] | None = None) -> None:
        self._snapshots = dict(snapshots or {})

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "snapshot",
                    "timeout",
                    True,
                    "Connector snapshot timed out; retry with the same cursor.",
                    timeout_seconds=60,
                ),
                AdapterFailureMode("snapshot", "validation", False, "Connector snapshot config is invalid."),
            ),
        )

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        configured = self._snapshots.get((request.tenant_id, request.connector_name, request.resource_name))
        if configured is not None:
            return configured
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=(),
            schema={"columns": []},
            cursor=request.cursor,
            source_watermark=request.request_id,
        )


class FakeConnectorAdapter(LocalConnectorAdapter):
    """Fake connector profile that preserves the local contract surface."""

    profile_name = "fake-connector"


_workflow_adapter: WorkflowAdapter = LocalWorkflowAdapter()
_stream_adapter: StreamAdapter = LocalStreamAdapter()
_search_adapter: SearchAdapter = LocalSearchAdapter()
_connector_adapter: ConnectorAdapter = LocalConnectorAdapter()
