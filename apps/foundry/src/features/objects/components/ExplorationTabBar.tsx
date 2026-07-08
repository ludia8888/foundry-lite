import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import { ChevronDown, CirclePlus, Layers, Search, X } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { objectTypeIconClass, type Exploration } from "../lib/explorer-model";

interface ExplorationTabBarProps {
  objectViews: FoundryLiteOntologyObjectView[];
  explorations: Exploration[];
  activeExplorationId: string | null;
  onSelectExploration: (explorationId: string) => void;
  onOpenExploration: (typeName: string) => void;
  onCloseExploration: (explorationId: string) => void;
  onOpenObjectSets: () => void;
}

function TypeIcon({
  apiName,
  className,
}: {
  apiName: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "flex size-[18px] shrink-0 items-center justify-center rounded-[3px] text-[10px] font-bold text-white",
        objectTypeIconClass(apiName),
        className,
      )}
    >
      {apiName.slice(0, 1).toUpperCase()}
    </span>
  );
}

function TypePickerContent({
  objectViews,
  onOpenExploration,
}: {
  objectViews: FoundryLiteOntologyObjectView[];
  onOpenExploration: (typeName: string) => void;
}) {
  return (
    <>
      <div className="section-label px-2 py-1.5">객체 타입 선택</div>
      {objectViews.length === 0 ? (
        <div className="px-2 py-2 text-xs text-muted-foreground">
          온톨로지에 객체 타입이 없습니다.
        </div>
      ) : (
        objectViews.map((view) => (
          <button
            key={view.apiName}
            type="button"
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent"
            onClick={() => onOpenExploration(view.apiName)}
          >
            <TypeIcon apiName={view.apiName} />
            <span className="flex-1 truncate">{view.displayName}</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              속성 {view.propertyCount}
            </span>
          </button>
        ))
      )}
    </>
  );
}

/** 최상단 브라우저식 탐색 탭바: 탭별 독립 탐색 + 새 탐색 + 우측 [탐색][목록] 버튼. */
export function ExplorationTabBar({
  objectViews,
  explorations,
  activeExplorationId,
  onSelectExploration,
  onOpenExploration,
  onCloseExploration,
  onOpenObjectSets,
}: ExplorationTabBarProps) {
  const viewsByApiName = new Map(
    objectViews.map((view) => [view.apiName, view]),
  );

  return (
    <div className="flex h-10 shrink-0 items-stretch border-b border-[#d5dce1] bg-[#ecf1f5]">
      {explorations.map((exploration) => {
        const view = viewsByApiName.get(exploration.typeName);
        const isActive = exploration.id === activeExplorationId;
        return (
          <div
            key={exploration.id}
            className={cn(
              "flex items-center gap-2 border-r border-[#d5dce1] pr-1.5 pl-3",
              isActive
                ? "-mb-px bg-white"
                : "text-[#404854] hover:bg-[#e2e9ee]",
            )}
          >
            <button
              type="button"
              className="flex min-w-0 items-center gap-2"
              onClick={() => onSelectExploration(exploration.id)}
            >
              <TypeIcon apiName={exploration.typeName} />
              <span
                className={cn(
                  "max-w-40 truncate text-[13px]",
                  isActive ? "font-semibold text-[#1c2127]" : "font-medium",
                )}
              >
                {view?.displayName ?? exploration.typeName} 탐색
              </span>
            </button>
            <button
              type="button"
              aria-label="탐색 탭 닫기"
              className="rounded p-0.5 text-[#5f6b7c] hover:bg-[#dce3e8] hover:text-foreground"
              onClick={() => onCloseExploration(exploration.id)}
            >
              <X className="size-3.5" />
            </button>
          </div>
        );
      })}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex items-center gap-2 border-r border-[#d5dce1] px-3 text-[13px] font-medium text-[#404854] hover:bg-[#e2e9ee]"
          >
            <CirclePlus className="size-4 text-[#5f6b7c]" />새 탐색
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-60 p-1">
          <TypePickerContent
            objectViews={objectViews}
            onOpenExploration={onOpenExploration}
          />
        </PopoverContent>
      </Popover>
      <div className="ml-auto flex items-center gap-2 pr-2">
        <Popover>
          <PopoverTrigger asChild>
            <button
              type="button"
              className="flex h-7 items-center gap-1.5 rounded border border-[#c5cdd4] bg-white px-2.5 text-[13px] font-medium text-[#404854] hover:bg-[#f6f8fa]"
            >
              <Search className="size-3.5 text-[#7961db]" />
              탐색
              <ChevronDown className="size-3 text-[#5f6b7c]" />
            </button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-60 p-1">
            <TypePickerContent
              objectViews={objectViews}
              onOpenExploration={onOpenExploration}
            />
          </PopoverContent>
        </Popover>
        <button
          type="button"
          className="flex h-7 items-center gap-1.5 rounded border border-[#c5cdd4] bg-white px-2.5 text-[13px] font-medium text-[#404854] hover:bg-[#f6f8fa]"
          onClick={onOpenObjectSets}
        >
          <Layers className="size-3.5 text-[#7961db]" />
          목록
          <ChevronDown className="size-3 text-[#5f6b7c]" />
        </button>
      </div>
    </div>
  );
}
