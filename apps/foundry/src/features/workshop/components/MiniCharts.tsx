import { useId } from "react";

import type { AggregateBucket } from "../lib/aggregate";
import { formatMetricValue } from "../lib/aggregate";

/** Blueprint 계열 카테고리 팔레트 (Palantir 차트 톤). */
export const CHART_COLORS = [
  "#2d72d2",
  "#238551",
  "#c87619",
  "#7961db",
  "#00847a",
  "#cd4246",
  "#5f6b7c",
  "#935610",
];

export function colorForIndex(index: number): string {
  return CHART_COLORS[index % CHART_COLORS.length];
}

interface BarChartMiniProps {
  buckets: AggregateBucket[];
  height?: number;
}

/** 세로 막대 차트 (Chart XY 대응). 값 라벨 + 카테고리 라벨 포함. */
export function BarChartMini({ buckets, height = 180 }: BarChartMiniProps) {
  if (buckets.length === 0) {
    return <ChartEmpty />;
  }
  const max = Math.max(...buckets.map((bucket) => bucket.value), 1);
  const barCount = buckets.length;
  const gap = 12;
  const barWidth = Math.max(24, Math.min(72, 320 / barCount - gap));
  const chartWidth = barCount * (barWidth + gap) + gap;
  const plotHeight = height - 34;

  return (
    <svg
      viewBox={`0 0 ${chartWidth} ${height}`}
      className="h-full max-h-[220px] w-full"
      preserveAspectRatio="xMidYMid meet"
      role="img"
    >
      <line
        x1={0}
        y1={plotHeight}
        x2={chartWidth}
        y2={plotHeight}
        stroke="#d5dce1"
        strokeWidth={1}
      />
      {buckets.map((bucket, index) => {
        const barHeight = Math.max(2, (bucket.value / max) * (plotHeight - 16));
        const x = gap + index * (barWidth + gap);
        const y = plotHeight - barHeight;
        return (
          <g key={bucket.label}>
            <text
              x={x + barWidth / 2}
              y={y - 4}
              textAnchor="middle"
              className="fill-[#404854] text-[10px] font-semibold"
            >
              {formatMetricValue(bucket.value)}
            </text>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={barHeight}
              rx={2}
              fill={colorForIndex(index)}
            />
            <text
              x={x + barWidth / 2}
              y={plotHeight + 14}
              textAnchor="middle"
              className="fill-[#5f6b7c] text-[10px]"
            >
              {truncate(bucket.label, 10)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

interface PieChartMiniProps {
  buckets: AggregateBucket[];
}

/** 도넛형 파이 차트 + 범례. */
export function PieChartMini({ buckets }: PieChartMiniProps) {
  const gradientId = useId();
  const total = buckets.reduce((sum, bucket) => sum + bucket.value, 0);
  if (buckets.length === 0 || total === 0) {
    return <ChartEmpty />;
  }
  const radius = 52;
  const inner = 30;
  const cx = 64;
  const cy = 64;
  let angle = -Math.PI / 2;

  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 128 128" className="size-32 shrink-0" role="img">
        {buckets.map((bucket, index) => {
          const fraction = bucket.value / total;
          const startAngle = angle;
          const endAngle = angle + fraction * Math.PI * 2;
          angle = endAngle;
          const path = donutSlice(cx, cy, radius, inner, startAngle, endAngle);
          return (
            <path
              key={`${gradientId}-${bucket.label}`}
              d={path}
              fill={colorForIndex(index)}
            />
          );
        })}
        <text
          x={cx}
          y={cy - 2}
          textAnchor="middle"
          className="fill-[#1c2127] text-[15px] font-bold"
        >
          {formatMetricValue(total)}
        </text>
        <text
          x={cx}
          y={cy + 12}
          textAnchor="middle"
          className="fill-[#8f99a8] text-[8px]"
        >
          전체
        </text>
      </svg>
      <ul className="min-w-0 flex-1 space-y-1">
        {buckets.map((bucket, index) => (
          <li
            key={bucket.label}
            className="flex items-center gap-2 text-[11px]"
          >
            <span
              className="size-2.5 shrink-0 rounded-[2px]"
              style={{ backgroundColor: colorForIndex(index) }}
            />
            <span className="min-w-0 flex-1 truncate text-[#404854]">
              {bucket.label}
            </span>
            <span className="shrink-0 font-mono text-[#5f6b7c]">
              {formatMetricValue(bucket.value)}
              <span className="ml-1 text-[#8f99a8]">
                {Math.round((bucket.value / total) * 100)}%
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChartEmpty() {
  return (
    <div className="flex h-32 items-center justify-center rounded border border-dashed border-[#d5dce1] text-[11px] text-muted-foreground">
      집계할 데이터가 없습니다.
    </div>
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function polar(cx: number, cy: number, radius: number, angle: number) {
  return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
}

function donutSlice(
  cx: number,
  cy: number,
  outer: number,
  inner: number,
  startAngle: number,
  endAngle: number,
): string {
  const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
  const outerStart = polar(cx, cy, outer, startAngle);
  const outerEnd = polar(cx, cy, outer, endAngle);
  const innerEnd = polar(cx, cy, inner, endAngle);
  const innerStart = polar(cx, cy, inner, startAngle);
  return [
    `M ${outerStart.x} ${outerStart.y}`,
    `A ${outer} ${outer} 0 ${largeArc} 1 ${outerEnd.x} ${outerEnd.y}`,
    `L ${innerEnd.x} ${innerEnd.y}`,
    `A ${inner} ${inner} 0 ${largeArc} 0 ${innerStart.x} ${innerStart.y}`,
    "Z",
  ].join(" ");
}
