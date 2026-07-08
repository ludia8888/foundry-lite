import type { MediaContentSearchHit } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { Loader2, Search } from "lucide-react";
import { useCallback, useState } from "react";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { MEDIA_SEARCH_TOP_K } from "./media-constants";
import { useScreenQuery } from "./use-screen-query";

const HIT_COLUMNS: readonly DataTableColumn<MediaContentSearchHit>[] = [
  {
    key: "text",
    header: "본문",
    className: "max-w-96 truncate",
    render: (hit) => <span title={hit.text}>{hit.text}</span>,
  },
  {
    key: "classification",
    header: "분류",
    render: (hit) => (
      <StatusPill intent="neutral">{hit.classification}</StatusPill>
    ),
  },
  {
    key: "generation",
    header: "인덱스 세대",
    isMono: true,
    render: (hit) => hit.index_generation,
  },
  {
    key: "source",
    header: "원본 버전",
    isMono: true,
    className: "max-w-56 truncate",
    render: (hit) => (
      <span title={hit.source_media_item_version_id}>
        {hit.source_media_item_version_id}
      </span>
    ),
  },
  {
    key: "page",
    header: "페이지",
    isMono: true,
    render: (hit) =>
      hit.page_number === null || hit.page_number === undefined
        ? "-"
        : String(hit.page_number),
  },
];

/** 인덱스된 미디어 콘텐츠 검색: 검색 상태를 파이프라인과 별도로 구분해 보여준다. */
export function MediaSearchPanel() {
  const [searchInput, setSearchInput] = useState("");
  const [submittedText, setSubmittedText] = useState<string | null>(null);

  const client = useFoundryLiteClient();
  const loadHits = useCallback(
    () =>
      client.media.content.search({
        text: submittedText,
        topK: MEDIA_SEARCH_TOP_K,
      }),
    [client, submittedText],
  );
  const search = useScreenQuery(["media", "search", submittedText], loadHits, {
    enabled: submittedText !== null && submittedText.length > 0,
  });
  const hits = search.data ?? [];

  const handleSearch = () => {
    const trimmed = searchInput.trim();
    if (trimmed.length === 0) return;
    setSubmittedText(trimmed);
  };

  const isSearching = search.isLoading || search.isRefreshing;

  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-center gap-2">
        <Search className="size-4 text-primary" />
        <span className="section-label">콘텐츠 검색</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          top_k={MEDIA_SEARCH_TOP_K}
        </span>
      </div>

      <form
        className="flex items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          handleSearch();
        }}
      >
        <Input
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="인덱스된 텍스트 검색"
          className="h-7 text-xs"
        />
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={searchInput.trim().length === 0 || isSearching}
        >
          {isSearching ? <Loader2 className="animate-spin" /> : <Search />}
          검색
        </Button>
      </form>

      {submittedText === null ? (
        <p className="text-xs text-muted-foreground">
          파이프라인에서 인덱스까지 완료한 콘텐츠가 검색 대상입니다.
        </p>
      ) : isSearching && !search.data ? (
        <LoadingState rowCount={3} />
      ) : search.error ? (
        <ErrorState error={search.error} onRetry={search.reload} />
      ) : hits.length > 0 ? (
        <DataTable
          columns={HIT_COLUMNS}
          rows={hits}
          rowKey={(hit) => hit.content_unit_id}
        />
      ) : (
        <EmptyState
          icon={Search}
          title="검색 결과가 없습니다"
          description={`"${submittedText}"에 매칭된 콘텐츠 유닛이 없습니다. 인덱스 세대가 최신인지 확인하세요.`}
        />
      )}
    </section>
  );
}
