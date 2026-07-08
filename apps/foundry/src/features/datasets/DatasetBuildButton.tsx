import type {
  LineageEdge,
  SourceManagedSyncRun,
  TransformRunResult,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Hammer, Loader2 } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { useScreenQuery } from "./use-screen-query";

interface DatasetBuildButtonProps {
  datasetRef: string;
  lineage: readonly LineageEdge[];
  onBuildComplete?: () => void;
}

type BuildResult = TransformRunResult | SourceManagedSyncRun;

/** lineage 에서 이 데이터셋을 출력하는 transform upstream apiName을 찾는다. */
function findTransformUpstream(
  lineage: readonly LineageEdge[],
  datasetRef: string,
): string | null {
  const targetIds = new Set([datasetRef, `dataset:${datasetRef}`]);
  const edge = lineage.find(
    (candidate) =>
      candidate.from_resource_type === "transform" &&
      (targetIds.has(candidate.to_resource_id) ||
        candidate.to_resource_id.startsWith(`${datasetRef}@`)),
  );
  if (!edge) return null;
  return edge.from_resource_id.replace(/^transform:/, "");
}

/**
 * Palantir 데이터셋 페이지의 그린 Build 버튼.
 * transform 출력이면 transforms.run, sync 대상이면 managedSyncs.startRun 을
 * 실행하고, 업스트림이 없으면 비활성 + "업스트림 없음" 툴팁을 보여준다.
 */
export function DatasetBuildButton({
  datasetRef,
  lineage,
  onBuildComplete,
}: DatasetBuildButtonProps) {
  const client = useFoundryLiteClient();

  const syncsQuery = useScreenQuery(
    ["datasets", "build", "managed-syncs"],
    () => client.sources.managedSyncs.list(),
  );

  const transformApiName = useMemo(
    () => findTransformUpstream(lineage, datasetRef),
    [lineage, datasetRef],
  );
  const matchedSync = useMemo(
    () =>
      (syncsQuery.data ?? []).find(
        (sync) => sync.targetDatasetRef === datasetRef,
      ) ?? null,
    [syncsQuery.data, datasetRef],
  );

  const build = useFoundryLiteMutation<BuildResult, void>(
    async () => {
      if (transformApiName) return client.transforms.run(transformApiName);
      if (matchedSync) {
        return client.sources.managedSyncs.startRun(
          matchedSync.syncName,
          { triggerType: "manual" },
          { idempotencyKey: idempotencyKey("dataset_build", datasetRef) },
        );
      }
      throw new Error("빌드할 업스트림이 없습니다.");
    },
    {
      lockKey: () => `datasets:build:${datasetRef}`,
      onSuccess: () => onBuildComplete?.(),
    },
  );

  const hasUpstream = transformApiName !== null || matchedSync !== null;
  const upstreamLabel = transformApiName
    ? `transform=${transformApiName}`
    : matchedSync
      ? `sync=${matchedSync.syncName}`
      : null;

  const buildButton = (
    <Button
      size="sm"
      disabled={!hasUpstream || build.isRunning}
      onClick={() => void build.execute(undefined)}
      className="bg-success text-success-foreground hover:bg-success/90"
    >
      {build.isRunning ? <Loader2 className="animate-spin" /> : <Hammer />}
      빌드
    </Button>
  );

  return (
    <div className="flex items-center gap-2">
      {build.error ? (
        <span
          className="max-w-48 truncate text-[11px] text-destructive"
          title={build.error.message}
        >
          빌드 실패: {build.error.message}
        </span>
      ) : null}
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">{buildButton}</span>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {hasUpstream ? `빌드 실행 — ${upstreamLabel}` : "업스트림 없음"}
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
