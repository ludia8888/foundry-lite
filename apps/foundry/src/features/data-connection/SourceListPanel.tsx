import type { SourceConnection } from "@foundry-lite/sdk";
import type { LucideIcon } from "lucide-react";
import {
  Database,
  FileSpreadsheet,
  Files,
  Image,
  Plus,
  Radio,
  Search,
  Webhook,
} from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { sourceTypeLabel, statusIntent, statusLabel } from "./source-model";

const KIND_ICONS: Record<string, LucideIcon> = {
  csv_upload: FileSpreadsheet,
  batch_file: Files,
  webhook_listener: Webhook,
  debezium_cdc: Radio,
  media_upload: Image,
  rest: Database,
};

interface SourceListPanelProps {
  sources: readonly SourceConnection[] | null;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  selectedSourceName: string | null;
  onSelect: (sourceName: string) => void;
  onCreate: () => void;
}

/** 좌측 소스 목록 패널: 검색 + kind 아이콘 + 상태 필. */
export function SourceListPanel({
  sources,
  isLoading,
  error,
  onRetry,
  selectedSourceName,
  onSelect,
  onCreate,
}: SourceListPanelProps) {
  const [search, setSearch] = useState("");
  const visibleSources = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return sources ?? [];
    return (sources ?? []).filter(
      (source) =>
        source.displayName.toLowerCase().includes(query) ||
        source.sourceName.toLowerCase().includes(query),
    );
  }, [sources, search]);

  return (
    <aside
      data-testid="source-list"
      className="flex max-h-80 w-full shrink-0 flex-col border-b lg:max-h-none lg:w-72 lg:border-r lg:border-b-0"
    >
      <div className="flex items-center justify-between px-3 pt-3 pb-2">
        <span className="section-label">
          소스{sources ? ` (${sources.length})` : ""}
        </span>
      </div>
      <div className="relative px-3 pb-2">
        <Search className="absolute top-1/2 left-5.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="소스 검색"
          className="h-8 pl-8 text-xs"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <ErrorState error={error} onRetry={onRetry} className="m-3" />
        ) : isLoading && !sources ? (
          <LoadingState rowCount={6} className="p-3" />
        ) : visibleSources.length === 0 ? (
          <EmptyState
            className="m-3"
            title={search ? "검색 결과가 없습니다" : "아직 소스가 없습니다"}
            description={
              search
                ? "다른 키워드로 검색해보세요."
                : "새 소스를 만들어 외부 데이터를 연결하세요."
            }
            action={
              search ? null : (
                <Button size="sm" onClick={onCreate}>
                  <Plus className="size-3.5" /> 새 소스
                </Button>
              )
            }
          />
        ) : (
          <ul>
            {visibleSources.map((source) => {
              const Icon = KIND_ICONS[source.kind] ?? Database;
              const isSelected = source.sourceName === selectedSourceName;
              return (
                <li key={source.sourceName}>
                  <button
                    type="button"
                    onClick={() => onSelect(source.sourceName)}
                    className={cn(
                      "flex w-full items-center gap-2.5 border-b px-3 py-2 text-left",
                      isSelected ? "bg-accent" : "hover:bg-muted/60",
                    )}
                  >
                    <span className="flex size-7 shrink-0 items-center justify-center rounded bg-primary/10">
                      <Icon className="size-3.5 text-primary" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">
                        {source.displayName}
                      </span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {sourceTypeLabel(source.kind)} ·{" "}
                        <span className="font-mono">{source.sourceName}</span>
                      </span>
                    </span>
                    <StatusPill intent={statusIntent(source.status)}>
                      {statusLabel(source.status)}
                    </StatusPill>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
