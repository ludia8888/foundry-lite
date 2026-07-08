import {
  AlignStartVertical,
  CircleMinus,
  Droplet,
  Expand,
  Eye,
  FilterX,
  Move,
  MousePointer2,
  Redo2,
  Search,
  Sparkles,
  Target,
  Undo2,
  type LucideIcon,
} from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { LineageColorMode } from "@/features/lineage/lineage-model";
import { COLOR_MODE_LABELS } from "@/features/lineage/lineage-model";
import { cn } from "@/lib/utils";

interface ToolButtonDef {
  id: string;
  label: string;
  icon: LucideIcon;
  onClick?: () => void;
  isActive?: boolean;
  disabledReason?: string;
}

interface LineageToolbarProps {
  colorMode: LineageColorMode;
  isLegendVisible: boolean;
  onChangeColorMode: (mode: LineageColorMode) => void;
  onToggleLegend: () => void;
  onRelayout: () => void;
  onClearSelection: () => void;
  onOpenSearch: () => void;
}

function ToolButton({ tool }: { tool: ToolButtonDef }) {
  const isDisabled = Boolean(tool.disabledReason);
  const button = (
    <button
      type="button"
      onClick={tool.onClick}
      disabled={isDisabled}
      className={cn(
        "flex w-14 flex-col items-center gap-1 rounded border border-transparent py-1.5",
        !isDisabled && "hover:border-border hover:bg-muted/60",
        isDisabled && "opacity-40",
        tool.isActive && "border-border bg-accent",
      )}
    >
      <tool.icon className="size-4" />
      <span className="text-[10px] leading-none text-muted-foreground">
        {tool.label}
      </span>
    </button>
  );
  if (!isDisabled) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>{button}</span>
      </TooltipTrigger>
      <TooltipContent side="right">{tool.disabledReason}</TooltipContent>
    </Tooltip>
  );
}

/** 좌측 대형 아이콘 툴바 — 라벨이 아이콘 아래 (스크린샷 그대로). */
export function LineageToolbar({
  colorMode,
  isLegendVisible,
  onChangeColorMode,
  onToggleLegend,
  onRelayout,
  onClearSelection,
  onOpenSearch,
}: LineageToolbarProps) {
  const FUTURE = "future — 그래프 편집 surface 미제공";
  const groups: ToolButtonDef[][] = [
    [
      {
        id: "move",
        label: "이동",
        icon: Move,
        isActive: true,
        onClick: () => {},
      },
      {
        id: "pick",
        label: "선택",
        icon: MousePointer2,
        disabledReason: FUTURE,
      },
    ],
    [
      { id: "layout", label: "정렬", icon: Sparkles, onClick: onRelayout },
      { id: "undo", label: "되돌리기", icon: Undo2, disabledReason: FUTURE },
      { id: "redo", label: "다시실행", icon: Redo2, disabledReason: FUTURE },
    ],
    [
      { id: "clean", label: "정리", icon: FilterX, onClick: onClearSelection },
      {
        id: "select-mode",
        label: "선택모드",
        icon: Target,
        disabledReason: FUTURE,
      },
      {
        id: "expand",
        label: "펼치기",
        icon: Expand,
        disabledReason: "모든 노드가 이미 그래프에 표시되어 있습니다",
      },
    ],
    [
      {
        id: "color",
        label: "색상",
        icon: Droplet,
        onClick: onToggleLegend,
        isActive: isLegendVisible,
      },
      { id: "find", label: "찾기", icon: Search, onClick: onOpenSearch },
      {
        id: "remove",
        label: "제거",
        icon: CircleMinus,
        disabledReason: FUTURE,
      },
      {
        id: "align",
        label: "맞춤",
        icon: AlignStartVertical,
        disabledReason: FUTURE,
      },
    ],
  ];

  return (
    <div className="flex w-[136px] shrink-0 flex-col gap-1 overflow-y-auto border-r bg-card p-1.5">
      {groups.map((group, groupIndex) => (
        <div
          key={groupIndex}
          className="flex flex-wrap gap-0.5 border-b pb-1.5 last:border-b-0"
        >
          {group.map((tool) => (
            <ToolButton key={tool.id} tool={tool} />
          ))}
        </div>
      ))}

      <div className="mt-1 space-y-1.5">
        <div className="section-label px-0.5">노드 색상 기준</div>
        <Select
          value={colorMode}
          onValueChange={(value) =>
            onChangeColorMode(value as LineageColorMode)
          }
        >
          <SelectTrigger size="sm" className="h-7 w-full text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(COLOR_MODE_LABELS).map(([mode, label]) => (
              <SelectItem key={mode} value={mode} className="text-xs">
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <button
          type="button"
          onClick={onToggleLegend}
          className={cn(
            "flex w-full items-center gap-1.5 rounded border px-2 py-1 text-[11px]",
            isLegendVisible ? "bg-accent" : "hover:bg-muted/60",
          )}
        >
          <Eye className="size-3.5" />
          범례 {isLegendVisible ? "숨기기" : "표시"}
        </button>
      </div>
    </div>
  );
}
