import { FolderTree, Hammer, Terminal } from "lucide-react";

import { FUTURE_FEATURES } from "../code-model";

interface CodeStatusBarProps {
  transformCount: number;
  datasetCount: number;
  phaseLabel: string;
}

/**
 * 하단 상태 바(code-view.png 최하단): 좌측 구현 항목 + 우측 future 항목 배지.
 * 미구현 IDE 고급 기능은 fabricate하지 않고 future로 정직하게 노출한다.
 */
export function CodeStatusBar({
  transformCount,
  datasetCount,
  phaseLabel,
}: CodeStatusBarProps) {
  return (
    <div className="flex h-6 shrink-0 items-center gap-3 border-t bg-[#F1F3F5] px-3 text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1">
        <FolderTree className="size-3" />
        Explorer
      </span>
      <span className="flex items-center gap-1">
        <Hammer className="size-3" />
        Build: {phaseLabel}
      </span>
      <span className="flex items-center gap-1">
        <Terminal className="size-3" />
        SQL
      </span>
      <span className="font-mono">transforms={transformCount}</span>
      <span className="font-mono">datasets={datasetCount}</span>

      <div className="ml-auto flex items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground/70">
          미구현(future):
        </span>
        {FUTURE_FEATURES.slice(0, 4).map((feature) => (
          <span
            key={feature}
            className="rounded-[2px] border border-dashed border-border px-1 text-[10px] text-muted-foreground/70"
            title="곧 제공 예정"
          >
            {feature}
          </span>
        ))}
      </div>
    </div>
  );
}
