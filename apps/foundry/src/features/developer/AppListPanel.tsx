import { AppWindow, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import {
  formatTimestamp,
  statusIntent,
  type OsdkApplicationRow,
} from "./developer-model";

interface AppListPanelProps {
  apps: OsdkApplicationRow[] | null;
  isLoading: boolean;
  error: FoundryLiteApiError | null;
  selectedAppId: string | null;
  onSelect: (appId: string) => void;
  onCreate: () => void;
  onRetry: () => void;
}

/** 좌측 애플리케이션 목록 패널 (검색 + 생성). */
export function AppListPanel({
  apps,
  isLoading,
  error,
  selectedAppId,
  onSelect,
  onCreate,
  onRetry,
}: AppListPanelProps) {
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return apps ?? [];
    return (apps ?? []).filter(
      (app) =>
        app.displayName.toLowerCase().includes(query) ||
        app.appApiName.toLowerCase().includes(query),
    );
  }, [apps, search]);

  return (
    <div className="flex w-80 shrink-0 flex-col border-r">
      <div className="space-y-2 border-b p-3">
        <div className="flex items-center justify-between">
          <span className="section-label">
            애플리케이션 ({apps?.length ?? 0})
          </span>
          <Button variant="outline" size="xs" onClick={onCreate}>
            <Plus className="size-3" /> 새 앱
          </Button>
        </div>
        <div className="relative">
          <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="애플리케이션 검색"
            className="h-8 pl-7 text-xs"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {isLoading ? (
          <LoadingState rowCount={5} className="p-1" />
        ) : error ? (
          <div className="p-1">
            <ErrorState error={error} onRetry={onRetry} />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={AppWindow}
            title={
              (apps?.length ?? 0) === 0
                ? "애플리케이션이 없습니다"
                : "검색 결과가 없습니다"
            }
            description={
              (apps?.length ?? 0) === 0
                ? "온톨로지 위에 SDK를 발급할 첫 애플리케이션을 만드세요."
                : undefined
            }
            action={
              (apps?.length ?? 0) === 0 ? (
                <Button size="sm" onClick={onCreate}>
                  <Plus className="size-3.5" /> 새 애플리케이션
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="space-y-1">
            {filtered.map((app) => {
              const isSelected = app.id === selectedAppId;
              return (
                <li key={app.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(app.id)}
                    className={cn(
                      "w-full rounded border px-2.5 py-2 text-left transition-colors",
                      isSelected
                        ? "border-primary/40 bg-accent"
                        : "border-transparent hover:bg-muted/60",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] font-medium">
                        {app.displayName}
                      </span>
                      <StatusPill intent={statusIntent(app.status)}>
                        {app.status}
                      </StatusPill>
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                      {app.appApiName}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {formatTimestamp(app.updatedAt)}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
