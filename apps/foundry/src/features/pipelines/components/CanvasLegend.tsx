import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import { NODE_TYPE_META } from "../pipeline-model";

const LEGEND_ORDER = [
  "dataset",
  "sql",
  "join",
  "union",
  "select_cast",
  "output_dataset",
] as const;

/** 툴바 밴드 우측 끝 '범례 ▾' 접이식 드롭다운: 기본 접힘, 펼치면 노드 타입별 색상 언어. */
export function CanvasLegend() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative w-32">
      <button
        type="button"
        className="flex h-7 w-full items-center justify-between rounded border border-[#C5CBD3] bg-white px-2.5 text-[12px] hover:bg-muted"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <span>범례</span>
        {isOpen ? (
          <ChevronUp className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        )}
      </button>
      {isOpen ? (
        <div className="absolute top-full right-0 z-20 mt-1 w-40 rounded border bg-card p-2 shadow-sm">
          <div className="space-y-1">
            {LEGEND_ORDER.map((type) => {
              const meta = NODE_TYPE_META[type];
              return (
                <div
                  key={type}
                  className="flex items-center gap-1.5 text-[11px]"
                >
                  <span
                    className={cn(
                      "size-2.5 shrink-0 rounded-[2px] border",
                      meta.isSolidHeader ? meta.headerClassName : "bg-card",
                    )}
                  />
                  <span className="truncate">{meta.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
