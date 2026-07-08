import type { Dataset } from "@foundry-lite/sdk";
import type {
  FoundryLiteDatasetExplorerState,
  FoundryLiteOperationsRunListState,
} from "@foundry-lite/sdk/react";
import {
  Activity,
  ChartNoAxesGantt,
  ChevronsDown,
  ChevronsUp,
  History,
  Table2,
  type LucideIcon,
} from "lucide-react";

import { BuildTimelineTab } from "@/features/lineage/BuildTimelineTab";
import { DataHealthTab } from "@/features/lineage/DataHealthTab";
import { HistoryTab } from "@/features/lineage/HistoryTab";
import { PreviewTab } from "@/features/lineage/PreviewTab";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { cn } from "@/lib/utils";

export type BottomDockTab = "preview" | "history" | "health" | "timeline";

const TAB_DEFS: { id: BottomDockTab; label: string; icon: LucideIcon }[] = [
  { id: "preview", label: "미리보기", icon: Table2 },
  { id: "history", label: "히스토리", icon: History },
  { id: "health", label: "데이터 상태", icon: Activity },
  { id: "timeline", label: "빌드 타임라인", icon: ChartNoAxesGantt },
];

interface LineageBottomDockProps {
  activeTab: BottomDockTab;
  isOpen: boolean;
  explorer: FoundryLiteDatasetExplorerState;
  runList: FoundryLiteOperationsRunListState;
  datasets: Dataset[];
  selectedRun: SelectedRunRef | null;
  onChangeTab: (tab: BottomDockTab) => void;
  onToggleOpen: () => void;
  onSelectRun: (run: SelectedRunRef) => void;
}

/** 하단 접이식 탭 도크: 미리보기 / 히스토리 / 데이터 상태 / 빌드 타임라인 (스크린샷 재현). */
export function LineageBottomDock({
  activeTab,
  isOpen,
  explorer,
  runList,
  datasets,
  selectedRun,
  onChangeTab,
  onToggleOpen,
  onSelectRun,
}: LineageBottomDockProps) {
  return (
    <div className="shrink-0 border-t bg-card">
      <div className="flex h-9 items-center">
        {TAB_DEFS.map((tab) => {
          const isActive = isOpen && activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => {
                onChangeTab(tab.id);
                if (!isOpen) onToggleOpen();
              }}
              className={cn(
                "flex h-full items-center gap-1.5 border-r px-3 text-xs",
                isActive
                  ? "-mt-px border-t-2 border-t-primary bg-background font-semibold text-primary"
                  : "text-muted-foreground hover:bg-muted/60",
              )}
            >
              <tab.icon className="size-3.5" />
              {tab.label}
            </button>
          );
        })}
        <button
          type="button"
          onClick={onToggleOpen}
          title={isOpen ? "패널 접기" : "패널 펼치기"}
          className="ml-auto flex h-full items-center px-3 text-muted-foreground hover:bg-muted/60"
        >
          {isOpen ? (
            <ChevronsDown className="size-4" />
          ) : (
            <ChevronsUp className="size-4" />
          )}
        </button>
      </div>

      {isOpen ? (
        <div className="h-64 overflow-auto border-t p-2">
          {activeTab === "preview" ? <PreviewTab explorer={explorer} /> : null}
          {activeTab === "history" ? (
            <HistoryTab
              runList={runList}
              datasets={datasets}
              selectedRun={selectedRun}
              onSelectRun={onSelectRun}
            />
          ) : null}
          {activeTab === "health" ? (
            <DataHealthTab explorer={explorer} onSelectRun={onSelectRun} />
          ) : null}
          {activeTab === "timeline" ? (
            <BuildTimelineTab
              runList={runList}
              datasets={datasets}
              onSelectRun={onSelectRun}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
