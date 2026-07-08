import { ChevronDown, ChevronUp, Table } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

/** 하단 '데이터 미리보기' 바: 기본 접힘, 클릭하면 확장 (공식 하단 바 구조). */
export function BottomDock({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="shrink-0 border-t bg-card">
      <button
        type="button"
        className="flex h-8 w-full items-center gap-1.5 px-2.5 text-left hover:bg-muted/50"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <Table className="size-3.5 text-muted-foreground" />
        <span className="text-[12px] font-semibold">데이터 미리보기</span>
        {isOpen ? (
          <ChevronDown className="ml-auto size-3.5 text-muted-foreground" />
        ) : (
          <ChevronUp className="ml-auto size-3.5 text-muted-foreground" />
        )}
      </button>
      {isOpen ? (
        <div className="h-64 overflow-y-auto border-t">{children}</div>
      ) : null}
    </div>
  );
}
