import { DIST_BAR_COLOR } from "../lib/explorer-model";

export type DistributionRow = {
  label: string;
  display: string;
  value: number;
};

/** 분포 행 리스트: 값 좌측 + 카운트 + 파란 막대 우측 정렬 (레퍼런스 dist 카드 문법). */
export function DistributionRows({ rows }: { rows: DistributionRow[] }) {
  const maxValue = rows.reduce((max, row) => Math.max(max, row.value), 0);
  return (
    <div className="space-y-1 px-4 py-3">
      {rows.map((row) => {
        const ratio = maxValue > 0 ? row.value / maxValue : 0;
        return (
          <div
            key={row.label}
            className="grid grid-cols-[minmax(0,1fr)_auto_6rem] items-center gap-3 py-1.5"
          >
            <span className="truncate text-[13px] text-[#383e47]">
              {row.label}
            </span>
            <span className="text-right text-[13px] tabular-nums text-[#5c7080]">
              {row.display}
            </span>
            <span className="flex items-center">
              <span
                className="h-2.5 rounded-[3px]"
                style={{
                  width: `${Math.max(ratio * 100, 6)}%`,
                  backgroundColor: DIST_BAR_COLOR,
                }}
              />
            </span>
          </div>
        );
      })}
    </div>
  );
}
