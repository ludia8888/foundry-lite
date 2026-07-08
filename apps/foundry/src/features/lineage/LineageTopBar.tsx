import type { Dataset } from "@foundry-lite/sdk";
import {
  ChevronDown,
  GitBranch,
  Save,
  Settings,
  Share2,
  Waypoints,
} from "lucide-react";

import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { datasetRefOf } from "@/features/lineage/lineage-model";

interface LineageTopBarProps {
  datasets: Dataset[];
  branches: string[];
  selectedDatasetRef: string | null;
  selectedBranch: string;
  onSelectDatasetRef: (ref: string) => void;
  onSelectBranch: (branch: string) => void;
}

function FutureAction({
  label,
  icon,
}: {
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>
          <Button size="sm" variant="ghost" disabled className="size-7 p-0">
            {icon}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{label} — future (SDK surface 미제공)</TooltipContent>
    </Tooltip>
  );
}

/** 상단 바: 리니지 글리프 + 데이터셋/브랜치 선택 + 저장(그린, future). */
export function LineageTopBar({
  datasets,
  branches,
  selectedDatasetRef,
  selectedBranch,
  onSelectDatasetRef,
  onSelectBranch,
}: LineageTopBarProps) {
  return (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b bg-card px-2">
      <div className="flex size-8 items-center justify-center rounded border bg-background">
        <Waypoints className="size-4 text-primary" />
      </div>
      <div className="text-[13px] font-semibold">데이터 리니지</div>
      <Separator orientation="vertical" className="h-5!" />

      <Select
        value={selectedDatasetRef ?? ""}
        onValueChange={onSelectDatasetRef}
      >
        <SelectTrigger size="sm" className="h-7 w-60 text-xs">
          <SelectValue placeholder="데이터셋 선택" />
        </SelectTrigger>
        <SelectContent>
          {datasets.map((dataset) => {
            const ref = datasetRefOf(dataset);
            return (
              <SelectItem key={ref} value={ref} className="font-mono text-xs">
                {ref}
              </SelectItem>
            );
          })}
        </SelectContent>
      </Select>

      <Select value={selectedBranch} onValueChange={onSelectBranch}>
        <SelectTrigger size="sm" className="h-7 w-36 text-xs">
          <GitBranch className="size-3.5 text-muted-foreground" />
          <SelectValue placeholder="브랜치" />
        </SelectTrigger>
        <SelectContent>
          {branches.map((branch) => (
            <SelectItem
              key={branch}
              value={branch}
              className="font-mono text-xs"
            >
              {branch}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <div className="ml-auto flex items-center gap-1.5">
        <RequestTelemetry />
        <FutureAction
          label="그래프 공유"
          icon={<Share2 className="size-3.5" />}
        />
        <FutureAction
          label="그래프 설정"
          icon={<Settings className="size-3.5" />}
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex items-center">
              <Button
                size="sm"
                disabled
                className="h-7 gap-1 rounded-r-none bg-success text-success-foreground hover:bg-success/90"
              >
                <Save className="size-3.5" />
                저장
              </Button>
              <Button
                size="sm"
                disabled
                className="h-7 rounded-l-none border-l border-success-foreground/20 bg-success px-1 text-success-foreground hover:bg-success/90"
              >
                <ChevronDown className="size-3.5" />
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            그래프 저장/공유는 future — 백엔드 surface가 아직 없습니다
          </TooltipContent>
        </Tooltip>
        <StatusPill intent="neutral">future</StatusPill>
      </div>
    </div>
  );
}
