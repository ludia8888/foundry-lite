from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    AdapterFailureContract,
    AdapterFailureMode,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

HN_BASE_URL = "https://hn.algolia.com"
WIKI_BASE_URL = "https://en.wikipedia.org"
WIKIMEDIA_EVENTSTREAM_URL = "https://stream.wikimedia.org/v2/stream/recentchange"
GITHUB_EVENTS_URL = "https://api.github.com/events"
USER_AGENT = "Foundry-lite-live-data-proof/1.0"


@dataclass(frozen=True)
class LiveProofConfig:
    storage_root: Path
    evidence_path: Path | None
    limit: int


class GitHubEventsConnectorAdapter:
    """Small live-proof adapter for GitHub's public events array endpoint."""

    profile_name = "live-github-events"

    def __init__(self, *, limit: int) -> None:
        self._limit = limit

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("snapshot", "timeout", True, "GitHub public events request timed out."),
                AdapterFailureMode("snapshot", "validation", False, "GitHub public events payload was invalid."),
            ),
        )

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        payload = _load_json_url(_github_events_url(self._limit))
        if not isinstance(payload, list):
            raise ValidationFailed("GitHub public events response must be a JSON array")
        rows = tuple(_github_event_row(item) for item in payload[: self._limit] if isinstance(item, Mapping))
        if not rows:
            raise ValidationFailed("GitHub public events returned no rows")
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=rows,
            schema={
                "columns": [
                    "id",
                    "event_type",
                    "actor_login",
                    "repo_name",
                    "created_at",
                    "source_url",
                    "payload_summary",
                ]
            },
            cursor=None,
            source_watermark=request.request_id,
        )


class WikipediaExtractConnectorAdapter:
    """Small live-proof adapter that normalizes live Wikipedia article text extracts."""

    profile_name = "live-wikipedia-extract"

    def __init__(self, *, titles: Sequence[str]) -> None:
        self._titles = tuple(title for title in titles if title)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("snapshot", "timeout", True, "Wikimedia extract request timed out."),
                AdapterFailureMode("snapshot", "validation", False, "Wikimedia extract payload was invalid."),
            ),
        )

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        if not self._titles:
            raise ValidationFailed("Wikipedia extract proof requires at least one title")
        payload = _load_json_url(_wikipedia_extract_url(self._titles))
        if not isinstance(payload, Mapping):
            raise ValidationFailed("Wikimedia extract response must be a JSON object")
        query = _mapping(payload.get("query"))
        pages = _mapping(query.get("pages"))
        rows = tuple(_wikipedia_extract_row(page) for page in pages.values() if isinstance(page, Mapping))
        rows = tuple(row for row in rows if row["extract"])
        if not rows:
            raise ValidationFailed("Wikimedia extract API returned no non-empty extracts")
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=rows,
            schema={"columns": ["pageid", "title", "extract", "retrieved_at", "source_url"]},
            cursor=None,
            source_watermark=request.request_id,
        )


class WikimediaRecentChangeStreamConnectorAdapter:
    """Live-proof adapter for Wikimedia's public Server-Sent Events stream."""

    profile_name = "live-wikimedia-recentchange-stream"

    def __init__(self, *, limit: int) -> None:
        self._limit = limit

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("snapshot", "timeout", True, "Wikimedia EventStream request timed out."),
                AdapterFailureMode("snapshot", "validation", False, "Wikimedia EventStream payload was invalid."),
            ),
        )

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        events = _load_wikimedia_recent_change_events(self._limit)
        rows = tuple(_wikimedia_recent_change_row(item) for item in events)
        if not rows:
            raise ValidationFailed("Wikimedia EventStream returned no usable enwiki article changes")
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=rows,
            schema={
                "columns": ("rcid", "pageid", "revid", "old_revid", "title", "timestamp", "user", "comment", "type")
            },
            cursor=None,
            source_watermark=request.request_id,
        )


def main(argv: list[str] | None = None) -> int:
    config = _config(_parser().parse_args(argv))
    summary = run_live_data_collection_proof(config)
    output = json.dumps(summary, indent=2, sort_keys=True)
    if config.evidence_path is not None:
        config.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        config.evidence_path.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def run_live_data_collection_proof(config: LiveProofConfig) -> dict[str, object]:
    dependencies = create_local_core_dependencies(storage_root=config.storage_root)
    foundry = FoundryLite(dependencies=replace(dependencies, connector_adapter=RestPullConnectorAdapter()))
    ctx = _context()
    _ensure_datasets(foundry, ctx)

    hn = _sync_hn_stories(foundry, ctx, config.limit)
    wiki_changes = _sync_wikipedia_recent_changes(foundry, ctx, dependencies, config.limit)
    titles = _titles_for_extracts(foundry.datasets.preview("raw.live_wikipedia_changes", ctx=ctx), config.limit)
    wiki_extracts = _sync_wikipedia_extracts(foundry, ctx, dependencies, titles)
    github_events = _sync_github_events(foundry, ctx, dependencies, config.limit)

    clean = _normalize_live_signals(foundry, ctx, config.storage_root)
    foundry.ontology.apply(str(_live_signal_ontology(config.storage_root)), ctx=ctx)
    index = foundry.objects.reindex("LiveSignal", ctx=ctx)
    search_index = foundry.objects.rebuild_search("LiveSignal", ctx=ctx)
    sample = _sample_live_signal(foundry, ctx)
    search = foundry.objects.query("LiveSignal", ctx=ctx, search_text=str(sample["title"]), limit=5)
    sample_signal_id = str(sample["signal_id"])
    _require_search_hit(search, sample_signal_id)
    agent = foundry.aip.run_agent_payload(
        payload=_agent_payload(f"live-data-collection-proof-{uuid4().hex[:8]}", sample_signal_id, str(sample["title"])),
        ctx=ctx,
    )
    if agent.run_status != "succeeded" or agent.ai_run_id is None:
        raise ValidationFailed("live data proof AIP run failed", details={"error": agent.error})
    ai_detail = foundry.operations.run_detail("ai", str(agent.ai_run_id), ctx=ctx)
    published = foundry.operations.publish_pending_outbox(ctx=ctx, limit=100)

    return {
        "status": "passed",
        "sources": {
            "hnAlgolia": _commit_summary(hn, HN_BASE_URL),
            "wikipediaRecentChanges": _commit_summary(wiki_changes, WIKIMEDIA_EVENTSTREAM_URL),
            "wikipediaExtracts": _commit_summary(wiki_extracts, WIKI_BASE_URL),
            "githubEvents": _commit_summary(github_events, GITHUB_EVENTS_URL),
        },
        "cleanLiveSignals": _commit_summary(clean, "transform:live_signals"),
        "objectIndex": index,
        "searchIndex": search_index,
        "searchHitIds": [item["objectId"] for item in search["items"]],
        "sampleSignal": sample,
        "aip": {
            "runStatus": agent.run_status,
            "aiRunId": agent.ai_run_id,
            "contextIds": list(agent.context_ids),
            "citationCount": len(agent.citations),
            "sourceResourceTypes": _ai_source_resource_types(ai_detail),
        },
        "outboxPublish": published,
    }


def _sync_hn_stories(foundry: FoundryLite, ctx: RequestContext, limit: int) -> Any:
    return foundry.datasets.sync_connector_snapshot(
        "raw.live_hn_stories",
        connector_name="rest",
        resource_name="hn_stories",
        ctx=ctx,
        rest=RestSourceConfig(
            base_url=HN_BASE_URL,
            resource_path=f"/api/v1/search_by_date?tags=story&hitsPerPage={limit}",
            auth=_user_agent_header(),
            pagination=RestPaginationConfig(items_path="hits", next_cursor_path=""),
            schema_columns=(
                "objectID",
                "story_id",
                "title",
                "url",
                "author",
                "created_at",
                "created_at_i",
                "points",
                "num_comments",
            ),
        ),
    )


def _sync_wikipedia_recent_changes(
    _foundry: FoundryLite,
    ctx: RequestContext,
    dependencies: Any,
    limit: int,
) -> Any:
    stream_foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=WikimediaRecentChangeStreamConnectorAdapter(limit=limit),
        )
    )
    return stream_foundry.datasets.sync_connector_snapshot(
        "raw.live_wikipedia_changes",
        connector_name="live_wikimedia_stream",
        resource_name="recentchange",
        ctx=ctx,
    )


def _sync_wikipedia_extracts(
    _foundry: FoundryLite,
    ctx: RequestContext,
    dependencies: Any,
    titles: Sequence[str],
) -> Any:
    extract_foundry = FoundryLite(
        dependencies=replace(dependencies, connector_adapter=WikipediaExtractConnectorAdapter(titles=titles))
    )
    return extract_foundry.datasets.sync_connector_snapshot(
        "raw.live_wikipedia_extracts",
        connector_name="live_wikipedia",
        resource_name="extracts",
        ctx=ctx,
    )


def _sync_github_events(_foundry: FoundryLite, ctx: RequestContext, dependencies: Any, limit: int) -> Any:
    github_foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=GitHubEventsConnectorAdapter(limit=limit),
        )
    )
    return github_foundry.datasets.sync_connector_snapshot(
        "raw.live_github_events",
        connector_name="live_github",
        resource_name="public_events",
        ctx=ctx,
    )


def _normalize_live_signals(foundry: FoundryLite, ctx: RequestContext, storage_root: Path) -> Any:
    transform = _live_signal_transform(storage_root)
    foundry.transforms.register(
        "live_signals",
        entrypoint=transform,
        inputs={
            "hn": "raw.live_hn_stories",
            "wiki_changes": "raw.live_wikipedia_changes",
            "wiki_extracts": "raw.live_wikipedia_extracts",
            "github": "raw.live_github_events",
        },
        output_dataset_ref="clean.live_signals",
        checks=[
            {"type": "unique", "column": "signal_id"},
            {"type": "not_null", "columns": ["signal_id", "source", "title"]},
        ],
        ctx=ctx,
    )
    return foundry.transforms.run("live_signals", ctx=ctx)


def _ensure_datasets(foundry: FoundryLite, ctx: RequestContext) -> None:
    foundry.datasets.ensure("raw.live_hn_stories", ctx=ctx, primary_key=["objectID"])
    foundry.datasets.ensure("raw.live_wikipedia_changes", ctx=ctx, primary_key=["rcid"])
    foundry.datasets.ensure("raw.live_wikipedia_extracts", ctx=ctx, primary_key=["pageid"])
    foundry.datasets.ensure("raw.live_github_events", ctx=ctx, primary_key=["id"])
    foundry.datasets.ensure("clean.live_signals", ctx=ctx, primary_key=["signal_id"])


def _live_signal_transform(storage_root: Path) -> Path:
    path = storage_root / "live-data-proof" / "transforms" / "live_signals.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
select
  'hn:' || cast("objectID" as varchar) as signal_id,
  'structured' as signal_kind,
  'hn_algolia' as source,
  cast(title as varchar) as title,
  cast(coalesce(url, '') as varchar) as url,
  cast(created_at as varchar) as observed_at,
  cast(coalesce(author, '') as varchar) as actor,
  '' as body_text
from {{ input('raw.live_hn_stories') }}
union all
select
  'wiki-change:' || cast(rcid as varchar) as signal_id,
  'structured' as signal_kind,
  'wikipedia_recentchanges' as source,
  cast(title as varchar) as title,
  'https://en.wikipedia.org/?curid=' || cast(pageid as varchar) as url,
  cast("timestamp" as varchar) as observed_at,
  cast(coalesce("user", '') as varchar) as actor,
  cast(coalesce(comment, '') as varchar) as body_text
from {{ input('raw.live_wikipedia_changes') }}
union all
select
  'wiki-extract:' || cast(pageid as varchar) as signal_id,
  'unstructured' as signal_kind,
  'wikipedia_extract' as source,
  cast(title as varchar) as title,
  cast(source_url as varchar) as url,
  cast(retrieved_at as varchar) as observed_at,
  '' as actor,
  cast(extract as varchar) as body_text
from {{ input('raw.live_wikipedia_extracts') }}
union all
select
  'github:' || cast(id as varchar) as signal_id,
  'structured' as signal_kind,
  'github_public_events' as source,
  cast(repo_name || ' ' || event_type as varchar) as title,
  cast(source_url as varchar) as url,
  cast(created_at as varchar) as observed_at,
  cast(coalesce(actor_login, '') as varchar) as actor,
  cast(coalesce(payload_summary, '') as varchar) as body_text
from {{ input('raw.live_github_events') }}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _live_signal_ontology(storage_root: Path) -> Path:
    path = storage_root / "live-data-proof" / "ontology" / "live-signal.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
objectTypes:
  - apiName: LiveSignal
    displayName: Live Signal
    primaryKey: signalId
    backing:
      dataset: clean.live_signals
      mode: snapshot
      primaryKeyColumns: [signal_id]
    properties:
      - apiName: signalId
        column: signal_id
        type: string
        indexed: true
        nullable: false
      - apiName: signalKind
        column: signal_kind
        type: string
        indexed: true
      - apiName: source
        column: source
        type: string
        indexed: true
      - apiName: title
        column: title
        type: string
        searchable: true
        indexed: true
      - apiName: url
        column: url
        type: string
      - apiName: observedAt
        column: observed_at
        type: string
      - apiName: actor
        column: actor
        type: string
      - apiName: bodyText
        column: body_text
        type: string
        searchable: true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _sample_live_signal(foundry: FoundryLite, ctx: RequestContext) -> dict[str, object]:
    rows = foundry.datasets.preview("clean.live_signals", ctx=ctx, limit=100)
    for row in rows:
        if row.get("source") == "wikipedia_extract" and row.get("body_text"):
            return {str(key): value for key, value in row.items()}
    if not rows:
        raise ValidationFailed("live signal transform produced no rows")
    return {str(key): value for key, value in rows[0].items()}


def _require_search_hit(search: Mapping[str, object], signal_id: str) -> None:
    items = search.get("items")
    if not isinstance(items, list):
        raise ValidationFailed("live signal search returned an invalid payload")
    ids = [str(item.get("objectId")) for item in items if isinstance(item, Mapping)]
    if signal_id not in ids:
        raise ValidationFailed("live signal search did not return the sampled object", details={"signalId": signal_id})


def _titles_for_extracts(rows: Sequence[Mapping[str, object]], limit: int) -> tuple[str, ...]:
    titles: list[str] = []
    for row in rows:
        title = row.get("title")
        if isinstance(title, str) and title:
            titles.append(title)
        if len(titles) >= limit:
            break
    return tuple(titles)


def _agent_payload(agent_run_id: str, signal_id: str, title: str) -> dict[str, object]:
    return {
        "agentRunId": agent_run_id,
        "agentVersionId": "agent.live-data-ops.v1",
        "modelAlias": "default-completion",
        "promptVersionId": "prompt-live-data-proof@v1",
        "userMessage": f"Summarize the live signal {signal_id}: {title}",
        "agentInstruction": "Answer as the Live Data Operations Copilot. Do not execute tools.",
        "securityPartition": "tenant-demo:internal",
        "allowedSecurityPartitions": ["tenant-demo:internal"],
        "stateJson": {"objectType": "LiveSignal", "objectId": signal_id},
        "outputSchema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "object"}},
            },
        },
        "dataClassification": "internal",
        "maxContextItems": 4,
        "maxContextTokens": 1200,
        "maxModelCalls": 1,
        "maxLoopIterations": 1,
        "maxOutputTokens": 512,
        "policyVersion": "policy-v1",
    }


def _context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-demo",
        actor_user_id="live-data-proof",
        request_id="req-live-data-proof",
        roles=DEMO_ADMIN_ROLES,
    )


def _user_agent_header() -> RestAuthConfig:
    return RestAuthConfig(mode="header", header_name="User-Agent", header_value=USER_AGENT)


def _load_json_url(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with urlopen(request, timeout=10) as response:  # noqa: S310  # nosec B310 - fixed public HTTPS proof.
        return json.loads(response.read(2_000_000).decode("utf-8"))


def _github_events_url(limit: int) -> str:
    return f"{GITHUB_EVENTS_URL}?{urlencode({'per_page': str(limit)})}"


def _wikipedia_extract_url(titles: Sequence[str]) -> str:
    query = urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "redirects": "1",
            "titles": "|".join(titles),
            "format": "json",
        }
    )
    return f"{WIKI_BASE_URL}/w/api.php?{query}"


def _load_wikimedia_recent_change_events(limit: int) -> tuple[Mapping[str, object], ...]:
    request = Request(
        WIKIMEDIA_EVENTSTREAM_URL,
        headers={"Accept": "text/event-stream", "User-Agent": USER_AGENT},
        method="GET",
    )
    events: list[Mapping[str, object]] = []
    started_at = time.monotonic()
    with urlopen(request, timeout=20) as response:  # noqa: S310  # nosec B310 - fixed public HTTPS proof.
        data_lines: list[str] = []
        while len(events) < limit and time.monotonic() - started_at < 20:
            raw_line = response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
                continue
            if line or not data_lines:
                continue
            payload = "\n".join(data_lines)
            data_lines = []
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, Mapping) and _is_usable_wikimedia_change(event):
                events.append(event)
    return tuple(events)


def _is_usable_wikimedia_change(event: Mapping[str, object]) -> bool:
    return event.get("wiki") == "enwiki" and event.get("namespace") == 0 and bool(event.get("title"))


def _github_event_row(item: Mapping[str, object]) -> Mapping[str, object]:
    actor = _mapping(item.get("actor"))
    repo = _mapping(item.get("repo"))
    payload = _mapping(item.get("payload"))
    repo_name = _text(repo.get("name"))
    return {
        "id": _text(item.get("id")),
        "event_type": _text(item.get("type")),
        "actor_login": _text(actor.get("login")),
        "repo_name": repo_name,
        "created_at": _text(item.get("created_at")),
        "source_url": f"https://github.com/{repo_name}" if repo_name else "https://github.com",
        "payload_summary": json.dumps(payload, sort_keys=True, default=str)[:1000],
    }


def _wikimedia_recent_change_row(item: Mapping[str, object]) -> Mapping[str, object]:
    revision = _mapping(item.get("revision"))
    return {
        "rcid": _text(item.get("id")),
        "pageid": _text(item.get("page_id")),
        "revid": _text(revision.get("new")),
        "old_revid": _text(revision.get("old")),
        "title": _text(item.get("title")),
        "timestamp": _text(item.get("timestamp")),
        "user": _text(item.get("user")),
        "comment": _text(item.get("comment")),
        "type": _text(item.get("type")),
    }


def _wikipedia_extract_row(page: Mapping[str, object]) -> Mapping[str, object]:
    pageid = _text(page.get("pageid"))
    return {
        "pageid": pageid,
        "title": _text(page.get("title")),
        "extract": _text(page.get("extract")),
        "retrieved_at": _text(page.get("touched")) or "live-proof-request",
        "source_url": f"https://en.wikipedia.org/?curid={pageid}",
    }


def _ai_source_resource_types(detail: Mapping[str, object]) -> list[str]:
    ai = detail.get("ai")
    if not isinstance(ai, Mapping):
        return []
    context = ai.get("contextItems")
    if not isinstance(context, list):
        return []
    values = [
        _text(cast(Mapping[str, object], item).get("source_resource_type"))
        for item in context
        if isinstance(item, Mapping)
    ]
    return sorted({value for value in values if value})


def _commit_summary(result: Any, source: str) -> dict[str, object]:
    return {
        "datasetRef": result.dataset_ref,
        "versionId": result.version_id,
        "rowCount": result.row_count,
        "source": source,
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live internet data collection through Foundry-lite.")
    parser.add_argument("--storage-root", type=Path, default=Path(".foundry-lite-live-data-proof"))
    parser.add_argument("--evidence-path", type=Path)
    parser.add_argument("--limit", type=int, default=3)
    return parser


def _config(args: argparse.Namespace) -> LiveProofConfig:
    if args.limit < 1 or args.limit > 10:
        raise ValidationFailed("live data proof limit must be between 1 and 10", details={"limit": args.limit})
    return LiveProofConfig(storage_root=args.storage_root, evidence_path=args.evidence_path, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
