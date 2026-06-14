from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.connector_adapter import (
    ConnectorAdapter,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
)
from foundry_lite.application.ports.search_adapter import (
    SearchAdapter,
    SearchDocument,
    SearchHit,
    SearchIndexMapping,
    SearchQuery,
)
from foundry_lite.application.ports.stream_adapter import StreamAdapter, StreamEvent, StreamPublishRequest
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter, WorkflowRun, WorkflowStartRequest


class LocalWorkflowAdapter:
    """In-memory workflow boundary for local/demo execution."""

    profile_name = "local-workflow"

    def __init__(self) -> None:
        self._runs_by_id: dict[str, WorkflowRun] = {}
        self._runs_by_idempotency: dict[str, WorkflowRun] = {}

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
        existing = self._runs_by_idempotency.get(request.idempotency_key)
        if existing is not None:
            return existing
        run = WorkflowRun(
            run_id=f"workflow_run_{len(self._runs_by_id) + 1}",
            workflow_name=request.workflow_name,
            status="succeeded",
            output={"workflow_name": request.workflow_name, "request_id": request.request_id},
        )
        self._runs_by_id[run.run_id] = run
        self._runs_by_idempotency[request.idempotency_key] = run
        return run

    def workflow_run(self, run_id: str) -> WorkflowRun | None:
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
        matches = [
            SearchHit(document_id=document.document_id, score=1.0, document=document)
            for key, document in sorted(self._documents.items())
            if self._matches_query(key, document, query)
        ]
        return matches[: query.limit]

    def _matches_query(self, key: tuple[str, str, str], document: SearchDocument, query: SearchQuery) -> bool:
        tenant_id, object_type, _ = key
        if tenant_id != query.tenant_id or object_type != query.object_type:
            return False
        return _matches_terms(document, query) and _matches_full_text(
            document, query, self._mappings.get((query.tenant_id, query.object_type))
        )


def _matches_terms(document: SearchDocument, query: SearchQuery) -> bool:
    return all(document.properties.get(name) == value for name, value in query.terms.items())


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


class LocalConnectorAdapter:
    """Configured in-memory connector boundary for local/demo execution."""

    profile_name = "local-connector"

    def __init__(self, snapshots: Mapping[tuple[str, str], ConnectorSnapshot] | None = None) -> None:
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
        configured = self._snapshots.get((request.connector_name, request.resource_name))
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
