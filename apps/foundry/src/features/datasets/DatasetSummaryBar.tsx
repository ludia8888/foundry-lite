import type { Dataset, DatasetVersion, LineageEdge } from "@foundry-lite/sdk";
import { ChevronDown, GitBranch, Table2 } from "lucide-react";
import { Link } from "react-router";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { DatasetBuildButton } from "./DatasetBuildButton";
import { formatByteSize, formatRowCount } from "./dataset-schema";

interface DatasetSummaryBarProps {
  dataset: Dataset | null;
  datasetRef: string;
  inspectedVersion: DatasetVersion | null;
  columnCount: number;
  rowCount: number | null;
  byteSize: number | null;
  lineage: readonly LineageEdge[];
  onBuildComplete?: () => void;
}

/**
 * Palantir Result 카드 스타일 요약 바:
 * 이름 + "N개 컬럼 · M행" evidence + 브랜치 셀렉터 + 그린 빌드 버튼 +
 * branch/version/transaction + lineage 링크.
 */
export function DatasetSummaryBar({
  dataset,
  datasetRef,
  inspectedVersion,
  columnCount,
  rowCount,
  byteSize,
  lineage,
  onBuildComplete,
}: DatasetSummaryBarProps) {
  const statusIntent = dataset?.status === "active" ? "success" : "neutral";
  const branchName = inspectedVersion?.branch ?? "main";
  const lineageHref = `/lineage?ref=${encodeURIComponent(datasetRef)}`;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-primary/40 bg-card p-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Table2 className="size-4 shrink-0 text-primary" />
          <span className="truncate text-[13px] font-semibold">
            {dataset?.name ?? datasetRef}
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {datasetRef}
          </span>
          {dataset ? (
            <StatusPill intent={statusIntent}>{dataset.status}</StatusPill>
          ) : null}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {columnCount}개 컬럼 · {formatRowCount(rowCount)}행 ·{" "}
          {formatByteSize(byteSize)}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {inspectedVersion ? (
          <div className="text-right font-mono text-[11px] text-muted-foreground">
            <div>
              branch={inspectedVersion.branch} · v
              {inspectedVersion.version_number} · schema_v
              {inspectedVersion.schema_version}
            </div>
            <div className="truncate" title={inspectedVersion.transaction_id}>
              tx={inspectedVersion.transaction_id}
            </div>
          </div>
        ) : null}
        <BranchSelector branchName={branchName} />
        <DatasetBuildButton
          datasetRef={datasetRef}
          lineage={lineage}
          onBuildComplete={onBuildComplete}
        />
        <Button size="sm" variant="outline" asChild>
          <Link to={lineageHref}>
            <GitBranch />
            리니지 보기
          </Link>
        </Button>
      </div>
    </div>
  );
}

/** 브랜치 셀렉터 — 현재 main 고정이라 표시 전용 (Blueprint select 문법). */
function BranchSelector({ branchName }: { branchName: string }) {
  return (
    <div
      aria-label="브랜치"
      title="브랜치 (main 고정)"
      className="flex h-8 cursor-default items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs font-medium"
    >
      <GitBranch className="size-3.5 text-muted-foreground" />
      <span className="font-mono">{branchName}</span>
      <ChevronDown className="size-3 text-muted-foreground" />
    </div>
  );
}
