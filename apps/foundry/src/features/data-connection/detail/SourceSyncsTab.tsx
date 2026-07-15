import type { SourceConnection, SourceManagedSync } from "@foundry-lite/sdk";
import { Plus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { statusIntent, statusLabel } from "../source-model";
import { useManagedSyncs } from "../use-source-queries";
import { NewSyncEditor } from "./NewSyncEditor";
import { SyncDetailView } from "./SyncDetailView";

interface SourceSyncsTabProps {
  source: SourceConnection;
  /** 탐색 탭 등에서 "동기화 생성"으로 진입할 때 true. */
  shouldStartCreating?: boolean;
  initialSyncName?: string | null;
  initialResourceName?: string | null;
  /** sync 상태가 바뀌면 상위 Source 투영 목록도 다시 읽는다. */
  onSyncStateChanged?: () => void | Promise<void>;
}

/**
 * 소스별 동기화 탭 (Palantir Edit syncs):
 * 좌측 이 소스의 sync 목록 + 우측 선택 sync 상세/새 동기화 편집기.
 */
export function SourceSyncsTab({
  source,
  shouldStartCreating = false,
  initialSyncName = null,
  initialResourceName = null,
  onSyncStateChanged,
}: SourceSyncsTabProps) {
  const syncsQuery = useManagedSyncs();
  const [selectedSyncName, setSelectedSyncName] = useState<string | null>(
    initialSyncName,
  );
  const [isCreating, setIsCreating] = useState(shouldStartCreating);

  const sourceSyncs = useMemo(
    () =>
      (syncsQuery.data ?? []).filter(
        (sync) => sync.sourceName === source.sourceName,
      ),
    [source.sourceName, syncsQuery.data],
  );

  const selectedSync =
    sourceSyncs.find((sync) => sync.syncName === selectedSyncName) ??
    sourceSyncs[0] ??
    null;

  const handleCreated = (sync: SourceManagedSync) => {
    setIsCreating(false);
    setSelectedSyncName(sync.syncName);
    void syncsQuery.reload();
  };

  if (syncsQuery.isLoading) {
    return <LoadingState className="max-w-xl" />;
  }
  if (syncsQuery.error) {
    return (
      <ErrorState
        error={syncsQuery.error}
        onRetry={() => void syncsQuery.reload()}
      />
    );
  }

  if (isCreating) {
    return (
      <NewSyncEditor
        source={source}
        initialResourceName={initialResourceName ?? undefined}
        onCreated={handleCreated}
        onCancel={() => setIsCreating(false)}
      />
    );
  }

  if (sourceSyncs.length === 0) {
    return (
      <EmptyState
        icon={RefreshCw}
        title="이 소스에는 아직 동기화가 없습니다"
        description="동기화는 소스에서 특정 데이터를 읽어 Foundry 데이터셋으로 가져오는 작업입니다."
        action={
          <Button size="sm" onClick={() => setIsCreating(true)}>
            <Plus className="size-3.5" /> 동기화 생성
          </Button>
        }
      />
    );
  }

  return (
    <div className="flex min-h-0 gap-4">
      <div className="w-52 shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <span className="section-label">동기화 {sourceSyncs.length}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-1.5"
            onClick={() => setIsCreating(true)}
          >
            <Plus className="size-3.5" /> 새 동기화
          </Button>
        </div>
        <div className="space-y-0.5">
          {sourceSyncs.map((sync) => (
            <button
              key={sync.syncName}
              type="button"
              onClick={() => setSelectedSyncName(sync.syncName)}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left",
                selectedSync?.syncName === sync.syncName
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-muted/60",
              )}
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium">
                  {sync.displayName}
                </span>
                <span className="block truncate font-mono text-[10px] text-muted-foreground">
                  {sync.targetDatasetRef ?? sync.targetMediaSetId ?? "—"}
                </span>
              </span>
              <StatusPill intent={statusIntent(sync.status)}>
                {statusLabel(sync.status)}
              </StatusPill>
            </button>
          ))}
        </div>
      </div>
      <div className="min-w-0 flex-1">
        {selectedSync ? (
          <SyncDetailView
            sync={selectedSync}
            isSourceDisabled={source.status === "disabled"}
            onRunStarted={() => {
              void syncsQuery.reload();
              void onSyncStateChanged?.();
            }}
            onSyncUpdated={(updated) => {
              setSelectedSyncName(updated.syncName);
              void syncsQuery.reload();
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
