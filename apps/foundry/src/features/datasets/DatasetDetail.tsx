import type { LucideIcon } from "lucide-react";
import { GitBranch, History, ShieldCheck, Table2 } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { cn } from "@/lib/utils";

import { DatasetPreviewTable } from "./DatasetPreviewTable";
import { DatasetSummaryBar } from "./DatasetSummaryBar";
import { LineagePanel } from "./LineagePanel";
import { QualityPanel } from "./QualityPanel";
import { SchemaPanel } from "./SchemaPanel";
import { VersionHistoryPanel } from "./VersionHistoryPanel";
import { parsePrimaryKey, parseSchemaColumns } from "./dataset-schema";
import type { DatasetExplorerState } from "./use-dataset-explorer";

type DetailTabId = "preview" | "versions" | "quality" | "lineage";

interface DetailTab {
  id: DetailTabId;
  label: string;
  icon: LucideIcon;
}

const DETAIL_TABS: readonly DetailTab[] = [
  { id: "preview", label: "미리보기", icon: Table2 },
  { id: "versions", label: "버전", icon: History },
  { id: "quality", label: "품질", icon: ShieldCheck },
  { id: "lineage", label: "리니지", icon: GitBranch },
];

interface DatasetDetailProps {
  explorer: DatasetExplorerState;
  namespace: string;
  name: string;
  onSelectVersion: (versionId: string) => void;
}

/**
 * 데이터셋 상세: 요약 바 + Palantir식 탭 스트립(미리보기/버전/품질/리니지).
 * 미리보기 탭은 acceptance에 따라 프리뷰 테이블과 스키마 패널을 항상 함께 보여준다.
 */
export function DatasetDetail({
  explorer,
  namespace,
  name,
  onSelectVersion,
}: DatasetDetailProps) {
  const [activeTab, setActiveTab] = useState<DetailTabId>("preview");
  const datasetRef = `${namespace}.${name}`;

  if (explorer.isLoading && !explorer.hasInspection) {
    return <LoadingState rowCount={8} className="p-4" />;
  }

  if (explorer.error) {
    return (
      <div className="p-4">
        <ErrorState error={explorer.error} onRetry={explorer.reload} />
      </div>
    );
  }

  const schemaColumns = parseSchemaColumns(explorer.schema);
  const primaryKey = parsePrimaryKey(explorer.schema);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <DatasetSummaryBar
        dataset={explorer.selectedDataset}
        datasetRef={datasetRef}
        inspectedVersion={explorer.inspectedVersion}
        columnCount={schemaColumns.length}
        rowCount={explorer.rowCount}
        byteSize={explorer.byteSize}
        lineage={explorer.lineage}
        onBuildComplete={() => void explorer.reload()}
      />

      <DetailTabStrip
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        isRefreshing={explorer.isRefreshing}
      />

      <div className="min-h-0 flex-1 overflow-auto">
        {activeTab === "preview" ? (
          <div className="flex h-full min-h-0 gap-3">
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <DatasetPreviewTable
                datasetName={explorer.selectedDataset?.name ?? name}
                rows={explorer.previewRows}
                schemaColumns={schemaColumns}
                totalRowCount={explorer.rowCount}
                inspectedVersion={explorer.inspectedVersion}
              />
            </div>
            <div className="flex w-80 shrink-0 flex-col">
              <SchemaPanel
                columns={schemaColumns}
                primaryKey={primaryKey}
                manifest={explorer.manifest}
              />
            </div>
          </div>
        ) : null}
        {activeTab === "versions" ? (
          <VersionHistoryPanel
            versions={explorer.versions}
            inspectedVersionId={explorer.inspectedVersion?.id ?? null}
            onSelectVersion={onSelectVersion}
          />
        ) : null}
        {activeTab === "quality" ? (
          <QualityPanel
            namespace={namespace}
            name={name}
            qualitySummary={explorer.qualitySummary}
          />
        ) : null}
        {activeTab === "lineage" ? (
          <LineagePanel lineage={explorer.lineage} datasetRef={datasetRef} />
        ) : null}
      </div>
    </div>
  );
}

interface DetailTabStripProps {
  activeTab: DetailTabId;
  onSelectTab: (tab: DetailTabId) => void;
  isRefreshing: boolean;
}

/** Palantir 하단 탭 스트립 스타일: 아이콘 + 라벨, 활성 탭은 파란 텍스트 + 흰 배경. */
function DetailTabStrip({
  activeTab,
  onSelectTab,
  isRefreshing,
}: DetailTabStripProps) {
  return (
    <div className="flex items-center border-y bg-secondary">
      {DETAIL_TABS.map((tab) => {
        const isActive = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onSelectTab(tab.id)}
            className={cn(
              "flex h-10 items-center gap-1.5 border-r px-4 text-[13px] font-medium",
              isActive
                ? "bg-card text-primary"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <tab.icon className="size-3.5" />
            {tab.label}
          </button>
        );
      })}
      {isRefreshing ? (
        <span className="ml-auto px-3 font-mono text-[11px] text-muted-foreground">
          새로고침 중…
        </span>
      ) : null}
    </div>
  );
}
