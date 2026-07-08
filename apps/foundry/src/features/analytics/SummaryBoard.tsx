import { ClipboardList } from "lucide-react";

import { PathBoard } from "./PathBoard";

interface SummaryBoardProps {
  datasetRef: string;
  previewRows: number;
  keptRows: number;
  columnCount: number;
  groupCount: number;
  versionId: string | null;
}

/**
 * SUMMARY board: 경로 최종 결과 evidence.
 * "N rows · M columns" + 적용 필터 후 행 수 + 집계 그룹 수 + version id (mono).
 * 재사용 출처: Contour "Calculate summary" + Dataset Explorer version evidence.
 */
export function SummaryBoard({
  datasetRef,
  previewRows,
  keptRows,
  columnCount,
  groupCount,
  versionId,
}: SummaryBoardProps) {
  const metrics = [
    { label: "프리뷰 행", value: `${previewRows} rows` },
    { label: "필터 유지 행", value: `${keptRows} rows` },
    { label: "컬럼", value: `${columnCount} columns` },
    { label: "집계 그룹", value: `${groupCount} groups` },
  ];

  return (
    <PathBoard icon={ClipboardList} label="SUMMARY · 결과 요약">
      <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">
        {metrics.map((metric) => (
          <div key={metric.label} className="bg-card px-3 py-2">
            <div className="section-label text-[10px]">{metric.label}</div>
            <div className="mt-0.5 font-mono text-[13px] font-semibold tabular-nums">
              {metric.value}
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
        <span>dataset={datasetRef}</span>
        {versionId ? (
          <span className="truncate" title={versionId}>
            version={versionId}
          </span>
        ) : null}
      </div>
    </PathBoard>
  );
}
