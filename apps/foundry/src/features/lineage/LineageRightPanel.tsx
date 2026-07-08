import type { FoundryLiteDatasetExplorerState } from "@foundry-lite/sdk/react";
import type { FoundryLiteOperationsRunListState } from "@foundry-lite/sdk/react";
import {
  Activity,
  CalendarClock,
  ChevronsLeft,
  ChevronsRight,
  Hammer,
  Info,
  Search,
  type LucideIcon,
} from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { NodeDetailPanel } from "@/features/lineage/NodeDetailPanel";
import { QualityPanel } from "@/features/lineage/QualityPanel";
import { RunEvidencePanel } from "@/features/lineage/RunEvidencePanel";
import { SearchPanel } from "@/features/lineage/SearchPanel";
import type {
  LineageGraphModel,
  LineageGraphNode,
} from "@/features/lineage/lineage-model";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { cn } from "@/lib/utils";

export type RightPanelTab =
  "search" | "detail" | "runs" | "quality" | "schedule";

const TAB_DEFS: { id: RightPanelTab; label: string; icon: LucideIcon }[] = [
  { id: "search", label: "검색", icon: Search },
  { id: "detail", label: "노드 상세", icon: Info },
  { id: "runs", label: "빌드 / 실행", icon: Hammer },
  { id: "quality", label: "품질 체크", icon: Activity },
  { id: "schedule", label: "일정", icon: CalendarClock },
];

interface LineageRightPanelProps {
  model: LineageGraphModel | null;
  selectedNode: LineageGraphNode | null;
  selectedRun: SelectedRunRef | null;
  activeTab: RightPanelTab;
  isPanelOpen: boolean;
  explorer: FoundryLiteDatasetExplorerState;
  runList: FoundryLiteOperationsRunListState;
  onChangeTab: (tab: RightPanelTab) => void;
  onTogglePanel: () => void;
  onSelectNode: (nodeId: string | null) => void;
  onSelectRun: (run: SelectedRunRef | null) => void;
  onMutated: () => void;
}

function SchedulePanel() {
  return (
    <div className="space-y-3 p-3">
      <div className="section-label">빌드 일정</div>
      <div className="flex items-center gap-2">
        <StatusPill intent="neutral">future</StatusPill>
        <span className="text-xs text-muted-foreground">
          SLA / 일정 자동화 surface 미제공
        </span>
      </div>
      <EmptyState
        icon={CalendarClock}
        title="일정 관리는 아직 지원되지 않습니다"
        description="빌드 일정과 SLA 정책 자동화는 향후 백엔드 surface가 추가되면 이 패널에서 관리합니다."
      />
    </div>
  );
}

/** 우측 세로 아이콘 탭 + 패널 (스크린샷의 우측 rail 재현). */
export function LineageRightPanel({
  model,
  selectedNode,
  selectedRun,
  activeTab,
  isPanelOpen,
  explorer,
  runList,
  onChangeTab,
  onTogglePanel,
  onSelectNode,
  onSelectRun,
  onMutated,
}: LineageRightPanelProps) {
  return (
    <div className="flex shrink-0">
      {isPanelOpen ? (
        <div className="flex w-80 flex-col overflow-y-auto border-l bg-card">
          {activeTab === "search" ? (
            <SearchPanel model={model} onSelectNode={onSelectNode} />
          ) : null}
          {activeTab === "detail" ? (
            <NodeDetailPanel
              model={model}
              node={selectedNode}
              explorer={explorer}
              onSelectNode={onSelectNode}
            />
          ) : null}
          {activeTab === "runs" ? (
            <RunEvidencePanel
              node={selectedNode}
              runList={runList}
              selectedRun={selectedRun}
              onSelectRun={onSelectRun}
              onMutated={onMutated}
            />
          ) : null}
          {activeTab === "quality" ? (
            <QualityPanel
              node={selectedNode}
              explorer={explorer}
              onSelectRun={onSelectRun}
              onChangeTab={onChangeTab}
            />
          ) : null}
          {activeTab === "schedule" ? <SchedulePanel /> : null}
        </div>
      ) : null}

      <div className="flex w-10 shrink-0 flex-col items-center gap-1 border-l bg-card py-2">
        {TAB_DEFS.map((tab) => (
          <Tooltip key={tab.id}>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => {
                  onChangeTab(tab.id);
                  if (!isPanelOpen) onTogglePanel();
                }}
                className={cn(
                  "flex size-8 items-center justify-center rounded border border-transparent",
                  activeTab === tab.id && isPanelOpen
                    ? "border-border bg-accent text-primary"
                    : "text-muted-foreground hover:bg-muted/60",
                )}
              >
                <tab.icon className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="left">{tab.label}</TooltipContent>
          </Tooltip>
        ))}
        <button
          type="button"
          onClick={onTogglePanel}
          title={isPanelOpen ? "패널 접기" : "패널 펼치기"}
          className="mt-auto flex size-8 items-center justify-center rounded text-muted-foreground hover:bg-muted/60"
        >
          {isPanelOpen ? (
            <ChevronsRight className="size-4" />
          ) : (
            <ChevronsLeft className="size-4" />
          )}
        </button>
      </div>
    </div>
  );
}
