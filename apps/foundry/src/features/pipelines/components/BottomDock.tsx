import { ChevronDown, ChevronUp, FlaskConical } from "lucide-react";
import type { KeyboardEvent, PointerEvent, ReactNode } from "react";
import { useRef, useState } from "react";

const MIN_PREVIEW_HEIGHT = 168;
const DEFAULT_PREVIEW_HEIGHT = 352;

function constrainedPreviewHeight(height: number): number {
  const maxHeight = Math.max(
    MIN_PREVIEW_HEIGHT,
    Math.round(window.innerHeight * 0.62),
  );
  return Math.min(Math.max(height, MIN_PREVIEW_HEIGHT), maxHeight);
}

/** 키보드와 포인터로 높이를 바꿀 수 있는 하단 no-commit preview pane. */
export function BottomDock({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [previewHeight, setPreviewHeight] = useState(DEFAULT_PREVIEW_HEIGHT);
  const dragStart = useRef<{ y: number; height: number } | null>(null);

  const handleResizePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    dragStart.current = { y: event.clientY, height: previewHeight };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const handleResizePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragStart.current) return;
    const nextHeight =
      dragStart.current.height + dragStart.current.y - event.clientY;
    setPreviewHeight(constrainedPreviewHeight(nextHeight));
  };
  const handleResizePointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    dragStart.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  const handleResizeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") {
      setPreviewHeight(MIN_PREVIEW_HEIGHT);
      return;
    }
    if (event.key === "End") {
      setPreviewHeight(constrainedPreviewHeight(Number.POSITIVE_INFINITY));
      return;
    }
    const delta = event.key === "ArrowUp" ? 24 : -24;
    setPreviewHeight((height) => constrainedPreviewHeight(height + delta));
  };

  return (
    <div className="shrink-0 border-t border-[#C5CBD3] bg-card">
      <button
        type="button"
        className="flex h-8 w-full items-center gap-1.5 px-2.5 text-left hover:bg-muted/50 focus-visible:z-10"
        aria-expanded={isOpen}
        aria-controls="pipeline-data-preview"
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <FlaskConical className="size-3.5 text-[#9B6D14]" />
        <span className="text-[12px] font-semibold">실제 데이터 미리보기</span>
        <span className="rounded border border-[#E2C98B] bg-[#FFF8E7] px-1.5 py-0.5 text-[10px] font-medium text-[#725B20]">
          미리보기 전용 · 출력 버전이 생성되지 않음
        </span>
        {isOpen ? (
          <ChevronDown className="ml-auto size-3.5 text-muted-foreground" />
        ) : (
          <ChevronUp className="ml-auto size-3.5 text-muted-foreground" />
        )}
      </button>
      {isOpen ? (
        <div
          id="pipeline-data-preview"
          className="relative overflow-hidden border-t border-[#C5CBD3]"
          style={{ height: previewHeight }}
        >
          <div
            role="separator"
            tabIndex={0}
            aria-label="데이터 미리보기 높이 조절"
            aria-orientation="horizontal"
            aria-valuemin={MIN_PREVIEW_HEIGHT}
            aria-valuenow={previewHeight}
            className="absolute inset-x-0 top-0 z-20 h-1 cursor-row-resize bg-transparent hover:bg-primary/50 focus-visible:bg-primary"
            onPointerDown={handleResizePointerDown}
            onPointerMove={handleResizePointerMove}
            onPointerUp={handleResizePointerEnd}
            onPointerCancel={handleResizePointerEnd}
            onKeyDown={handleResizeKeyDown}
          />
          <div className="h-full pt-1">{children}</div>
        </div>
      ) : null}
    </div>
  );
}
