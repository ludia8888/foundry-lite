import { ArrowRight } from "lucide-react";

import { cn } from "@/lib/utils";

interface ProductDiagramProps {
  nodes: readonly string[];
  className?: string;
}

/**
 * 상세 페이지 '스크린샷 자리'.
 *
 * 실제 install 스크린샷은 없으므로(reference_only), Palantir use-case 다이어그램
 * 스타일(연회색 배경 + 박스 노드 + 화살표)의 플로우 플레이스홀더로 대체한다.
 * fabricate가 아니라 제품의 데이터 흐름을 도식화한 것임을 배지로 명시한다.
 */
export function ProductDiagram({ nodes, className }: ProductDiagramProps) {
  return (
    <div
      className={cn(
        "relative flex aspect-video w-full items-center justify-center rounded border bg-muted/40 px-4",
        className,
      )}
    >
      <span className="section-label absolute top-2 left-3 text-muted-foreground/70">
        데이터 흐름 개요
      </span>
      <div className="flex flex-wrap items-center justify-center gap-2">
        {nodes.map((node, index) => (
          <div key={node} className="flex items-center gap-2">
            <div className="flex h-16 w-28 items-center justify-center rounded border border-border bg-background px-2 text-center text-[11px] leading-tight font-medium text-foreground/80 shadow-sm">
              {node}
            </div>
            {index < nodes.length - 1 ? (
              <ArrowRight className="size-3.5 shrink-0 text-muted-foreground/50" />
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
