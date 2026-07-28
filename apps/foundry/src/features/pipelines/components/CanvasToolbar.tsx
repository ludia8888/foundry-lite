import type { Dataset, PipelineNodeType } from "@foundry-lite/sdk";
import {
  Blend,
  BookOpenCheck,
  Braces,
  ChevronDown,
  CircleDot,
  Columns3,
  Combine,
  Database,
  Download,
  Eraser,
  LayoutGrid,
  Move,
  MousePointer2,
  Table2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

import { NODE_TYPE_META } from "../pipeline-model";
import { CanvasLegend } from "./CanvasLegend";
import type { CanvasInteractionMode } from "./PipelineFlowCanvas";

interface CanvasToolbarProps {
  datasets: readonly Dataset[];
  isDatasetsLoading: boolean;
  hasSelection: boolean;
  interactionMode: CanvasInteractionMode;
  onChangeInteractionMode: (mode: CanvasInteractionMode) => void;
  onSelectAll: () => void;
  onRemoveSelection: () => void;
  onAddDataset: (dataset: Dataset) => void;
  onAddTransform: (type: PipelineNodeType) => void;
  onAutoLayout: () => void;
  onOpenCatalog: () => void;
}

/** 변환 클러스터: 공식 툴바처럼 고유색 아이콘 버튼이 나란히 놓인다. */
const TRANSFORM_TYPES: readonly {
  type: PipelineNodeType;
  icon: LucideIcon;
  colorClassName: string;
}[] = [
  { type: "sql", icon: Braces, colorClassName: "text-[#2D72D2]" },
  { type: "join", icon: Blend, colorClassName: "text-[#7961DB]" },
  { type: "union", icon: Combine, colorClassName: "text-[#D9822B]" },
  { type: "select_cast", icon: Columns3, colorClassName: "text-[#DB2C6F]" },
];

/**
 * 캔버스 위 상단 고정 밴드 (공식 툴바): 회색 배경 한 줄에
 * [Tools|선택|제거|정렬] | [데이터셋 추가] [(x) 파라미터 ▾] | [변환|편집|출력] · 우측 끝 [범례 ▾].
 */
export function CanvasToolbar({
  datasets,
  isDatasetsLoading,
  hasSelection,
  interactionMode,
  onChangeInteractionMode,
  onSelectAll,
  onRemoveSelection,
  onAddDataset,
  onAddTransform,
  onAutoLayout,
  onOpenCatalog,
}: CanvasToolbarProps) {
  const [isDatasetOpen, setIsDatasetOpen] = useState(false);

  return (
    <div className="flex shrink-0 items-start gap-2.5 bg-[#EEF1F4] px-3 pt-1.5 pb-1">
      <ToolCluster label="Tools">
        <div className="flex overflow-hidden rounded border border-[#C5CBD3]">
          <ToolIconButton
            title="패닝 모드: 드래그로 그래프 이동"
            isActive={interactionMode === "pan"}
            onClick={() => onChangeInteractionMode("pan")}
          >
            <Move className="size-4" />
          </ToolIconButton>
          <ToolIconButton
            title="드래그 선택 모드: 드래그로 여러 노드 선택"
            isActive={interactionMode === "select"}
            className="border-l border-[#C5CBD3]"
            onClick={() => onChangeInteractionMode("select")}
          >
            <MousePointer2 className="size-4" />
          </ToolIconButton>
        </div>
      </ToolCluster>

      <ToolCluster label="선택">
        <ToolIconButton
          title="그래프의 모든 노드 선택"
          onClick={onSelectAll}
          className="rounded border border-[#C5CBD3]"
        >
          <CircleDot className="size-4" />
        </ToolIconButton>
      </ToolCluster>

      <ToolCluster label="제거">
        <ToolIconButton
          title={
            hasSelection
              ? "선택한 노드를 파이프라인에서 제거"
              : "제거할 노드를 먼저 선택하세요"
          }
          isDisabled={!hasSelection}
          onClick={onRemoveSelection}
          className="rounded border border-[#C5CBD3]"
        >
          <Eraser className="size-4" />
        </ToolIconButton>
      </ToolCluster>

      <ToolCluster label="정렬">
        <ToolIconButton
          title="노드를 균등하게 분산 정렬"
          onClick={onAutoLayout}
          className="rounded border border-[#C5CBD3]"
        >
          <LayoutGrid className="size-4" />
        </ToolIconButton>
      </ToolCluster>

      <Separator
        orientation="vertical"
        className="!h-8 self-start bg-[#C5CBD3]"
      />

      <div className="flex items-center gap-1.5 pt-0.5">
        <Popover open={isDatasetOpen} onOpenChange={setIsDatasetOpen}>
          <PopoverTrigger asChild>
            <Button
              size="sm"
              variant="outline"
              className="h-7 border-[#C5CBD3] bg-white px-2.5 text-[12px]"
            >
              <Download className="size-3.5" />
              데이터셋 추가
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 p-0">
            <Command>
              <CommandInput
                placeholder="데이터셋 검색..."
                className="h-8 text-[12px]"
              />
              <CommandList>
                <CommandEmpty>
                  {isDatasetsLoading
                    ? "불러오는 중..."
                    : "데이터셋이 없습니다."}
                </CommandEmpty>
                <CommandGroup heading="시드된 데이터셋">
                  {datasets.map((dataset) => (
                    <CommandItem
                      key={dataset.id}
                      value={`${dataset.namespace}.${dataset.name}`}
                      className="text-[12px]"
                      onSelect={() => {
                        onAddDataset(dataset);
                        setIsDatasetOpen(false);
                      }}
                    >
                      <Table2 className="size-3.5 text-muted-foreground" />
                      <span className="font-mono text-[11px]">
                        {dataset.namespace}.{dataset.name}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>

        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label="노드 카탈로그"
          className="h-7 rounded-[2px] border-[#AEB6C1] bg-white px-2.5 text-[12px]"
          onClick={onOpenCatalog}
        >
          <BookOpenCheck className="size-3.5 text-[#147D75]" />
          변환
          <ChevronDown className="size-3" />
        </Button>
      </div>

      <Separator
        orientation="vertical"
        className="!h-8 self-start bg-[#C5CBD3]"
      />

      <ToolCluster label="변환">
        <div className="flex gap-0.5">
          {TRANSFORM_TYPES.map(({ type, icon: Icon, colorClassName }) => (
            <ToolIconButton
              key={type}
              title={`${NODE_TYPE_META[type].label} 노드 추가`}
              onClick={() => onAddTransform(type)}
              className="rounded border border-[#C5CBD3]"
            >
              <Icon className={cn("size-4", colorClassName)} />
            </ToolIconButton>
          ))}
        </div>
      </ToolCluster>

      <ToolCluster label="출력 추가">
        <ToolIconButton
          title="데이터셋 출력 노드 추가"
          onClick={() => onAddTransform("output_dataset")}
          className="rounded border border-[#C5CBD3]"
        >
          <Database className="size-4 text-[#29A634]" />
        </ToolIconButton>
      </ToolCluster>

      <div className="ml-auto pt-0.5">
        <CanvasLegend />
      </div>
    </div>
  );
}

function ToolCluster({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-0.5">
      {children}
      <span className="text-[10px] leading-none text-muted-foreground">
        {label}
      </span>
    </div>
  );
}

function ToolIconButton({
  title,
  isActive,
  isDisabled,
  className,
  onClick,
  children,
}: {
  title: string;
  isActive?: boolean;
  isDisabled?: boolean;
  className?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={isDisabled}
      className={cn(
        "flex size-7 items-center justify-center text-foreground transition-colors",
        isActive ? "bg-[#DCE0E5]" : "bg-white hover:bg-muted",
        isDisabled ? "cursor-not-allowed opacity-40" : null,
        className,
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
