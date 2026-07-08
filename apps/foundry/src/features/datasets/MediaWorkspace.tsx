import type { MediaSet } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { FileStack } from "lucide-react";
import { useCallback } from "react";

import { StatusPill } from "@/components/shared/StatusPill";

import { MediaPipelinePanel } from "./MediaPipelinePanel";
import { MediaProcessingRunsPanel } from "./MediaProcessingRunsPanel";
import { MediaSearchPanel } from "./MediaSearchPanel";
import { MEDIA_PROCESSING_RUN_LIMIT } from "./media-constants";
import { useScreenQuery } from "./use-screen-query";

interface MediaWorkspaceProps {
  mediaSet: MediaSet;
}

/** 미디어 세트 작업 공간: 세트 요약 + upload→commit→process→search 파이프라인 + 검색 + 처리 이력. */
export function MediaWorkspace({ mediaSet }: MediaWorkspaceProps) {
  const client = useFoundryLiteClient();
  const loadRuns = useCallback(
    () =>
      client.media.processingRuns.list({ limit: MEDIA_PROCESSING_RUN_LIMIT }),
    [client],
  );
  const runsQuery = useScreenQuery(
    ["media", "processing-runs", mediaSet.media_set_id],
    loadRuns,
  );

  const handlePipelineCompleted = useCallback(() => {
    void runsQuery.reload();
  }, [runsQuery.reload]);

  return (
    <div className="space-y-3 p-4">
      <MediaSetSummaryBar mediaSet={mediaSet} />
      <MediaPipelinePanel
        mediaSet={mediaSet}
        onPipelineCompleted={handlePipelineCompleted}
      />
      <MediaSearchPanel />
      <MediaProcessingRunsPanel runsQuery={runsQuery} />
    </div>
  );
}

function MediaSetSummaryBar({ mediaSet }: { mediaSet: MediaSet }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-primary/40 bg-card p-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <FileStack className="size-4 shrink-0 text-primary" />
          <span className="truncate text-[13px] font-semibold">
            {mediaSet.name}
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {mediaSet.namespace}.{mediaSet.name}
          </span>
          <StatusPill intent="info">{mediaSet.schema_type}</StatusPill>
          <StatusPill intent="neutral">{mediaSet.classification}</StatusPill>
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 font-mono text-[11px] text-muted-foreground">
          <span title={mediaSet.media_set_id}>id={mediaSet.media_set_id}</span>
          <span>primary_format={mediaSet.primary_format}</span>
          <span>tx_policy={mediaSet.transaction_policy}</span>
          <span>
            storage={mediaSet.storage_profile} · processing=
            {mediaSet.processing_profile}
          </span>
        </div>
      </div>
    </div>
  );
}
