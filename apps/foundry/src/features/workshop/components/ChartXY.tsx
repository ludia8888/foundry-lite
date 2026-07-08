import type { ChartType } from "../lib/app-model";
import type { CrossAggregate } from "../lib/aggregate";
import { formatMetricValue } from "../lib/aggregate";
import { colorForIndex } from "./MiniCharts";

interface ChartXYProps {
  data: CrossAggregate;
  chartType: ChartType;
  xLabel: string;
  yLabel: string;
}

const W = 480;
const H = 300;

/**
 * Chart XY (Palantir 클론): 축 제목·눈금·그리드 + 시리즈 누적 + 범례.
 * 유형: 막대(수직) / 가로막대 / 라인 / 영역(누적) / 산점도.
 */
export function ChartXY({ data, chartType, xLabel, yLabel }: ChartXYProps) {
  const { categories, series, matrix } = data;
  if (categories.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center rounded border border-dashed border-[#d5dce1] text-[11px] text-muted-foreground">
        집계할 데이터가 없습니다.
      </div>
    );
  }

  const stacked =
    chartType === "bar" ||
    chartType === "horizontalBar" ||
    chartType === "area";
  const maxValue = stacked
    ? Math.max(...data.categoryTotals, 1)
    : Math.max(...matrix.flat(), 1);
  const niceMax = niceCeil(maxValue);
  const ticks = axisTicks(niceMax, 4);

  const chart =
    chartType === "horizontalBar" ? (
      <HorizontalBars
        categories={categories}
        series={series}
        matrix={matrix}
        niceMax={niceMax}
        ticks={ticks}
        xLabel={yLabel}
      />
    ) : (
      <VerticalPlot
        chartType={chartType}
        categories={categories}
        series={series}
        matrix={matrix}
        niceMax={niceMax}
        ticks={ticks}
        xLabel={xLabel}
        yLabel={yLabel}
      />
    );

  return (
    <div className="space-y-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-full max-h-[280px] w-full"
        role="img"
      >
        {chart}
      </svg>
      {series.length > 1 || series[0] !== "전체" ? (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 px-2">
          {series.map((label, index) => (
            <li key={label} className="flex items-center gap-1.5 text-[11px]">
              <span
                className="size-2.5 rounded-[2px]"
                style={{ backgroundColor: colorForIndex(index) }}
              />
              <span className="text-[#404854]">{label}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function VerticalPlot({
  chartType,
  categories,
  series,
  matrix,
  niceMax,
  ticks,
  xLabel,
  yLabel,
}: {
  chartType: ChartType;
  categories: string[];
  series: string[];
  matrix: number[][];
  niceMax: number;
  ticks: number[];
  xLabel: string;
  yLabel: string;
}) {
  const ml = 48;
  const mr = 14;
  const mt = 12;
  const mb = 40;
  const plotW = W - ml - mr;
  const plotH = H - mt - mb;
  const yFor = (value: number) => mt + plotH - (value / niceMax) * plotH;
  const band = plotW / categories.length;
  const cx = (index: number) => ml + band * (index + 0.5);

  return (
    <g>
      {/* Y 그리드 + 눈금 */}
      {ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={ml}
            y1={yFor(tick)}
            x2={ml + plotW}
            y2={yFor(tick)}
            stroke="#eef1f4"
          />
          <text
            x={ml - 6}
            y={yFor(tick) + 3}
            textAnchor="end"
            className="fill-[#8f99a8] text-[9px]"
          >
            {formatMetricValue(tick)}
          </text>
        </g>
      ))}
      {/* 축 */}
      <line x1={ml} y1={mt} x2={ml} y2={mt + plotH} stroke="#c5ccd3" />
      <line
        x1={ml}
        y1={mt + plotH}
        x2={ml + plotW}
        y2={mt + plotH}
        stroke="#c5ccd3"
      />

      {chartType === "bar"
        ? categories.map((category, ci) => {
            const barW = Math.min(48, band * 0.62);
            const x = cx(ci) - barW / 2;
            let cursorY = mt + plotH;
            return (
              <g key={category}>
                {series.map((_, si) => {
                  const value = matrix[ci][si];
                  const h = (value / niceMax) * plotH;
                  cursorY -= h;
                  return h > 0 ? (
                    <rect
                      key={si}
                      x={x}
                      y={cursorY}
                      width={barW}
                      height={h}
                      fill={colorForIndex(si)}
                    />
                  ) : null;
                })}
                <text
                  x={cx(ci)}
                  y={yFor(sum(matrix[ci])) - 4}
                  textAnchor="middle"
                  className="fill-[#404854] text-[9px] font-semibold"
                >
                  {formatMetricValue(sum(matrix[ci]))}
                </text>
              </g>
            );
          })
        : null}

      {chartType === "area"
        ? series.map((_, si) => {
            const lower = categories.map((_, ci) =>
              sumUpTo(matrix[ci], si - 1),
            );
            const upper = categories.map((_, ci) => sumUpTo(matrix[ci], si));
            const top = upper.map((value, ci) => `${cx(ci)},${yFor(value)}`);
            const bottom = lower
              .map((value, ci) => `${cx(ci)},${yFor(value)}`)
              .reverse();
            return (
              <polygon
                key={si}
                points={[...top, ...bottom].join(" ")}
                fill={colorForIndex(si)}
                fillOpacity={0.55}
                stroke={colorForIndex(si)}
                strokeWidth={1}
              />
            );
          })
        : null}

      {chartType === "line"
        ? series.map((_, si) => {
            const points = categories
              .map((_, ci) => `${cx(ci)},${yFor(matrix[ci][si])}`)
              .join(" ");
            return (
              <g key={si}>
                <polyline
                  points={points}
                  fill="none"
                  stroke={colorForIndex(si)}
                  strokeWidth={2}
                />
                {categories.map((_, ci) => (
                  <circle
                    key={ci}
                    cx={cx(ci)}
                    cy={yFor(matrix[ci][si])}
                    r={2.5}
                    fill={colorForIndex(si)}
                  />
                ))}
              </g>
            );
          })
        : null}

      {chartType === "scatter"
        ? categories.map((category, ci) =>
            series.map((_, si) => {
              const value = matrix[ci][si];
              return value > 0 ? (
                <circle
                  key={`${category}-${si}`}
                  cx={cx(ci)}
                  cy={yFor(value)}
                  r={3.5}
                  fill={colorForIndex(si)}
                  fillOpacity={0.8}
                />
              ) : null;
            }),
          )
        : null}

      {/* X 카테고리 라벨 */}
      {categories.map((category, ci) => (
        <text
          key={category}
          x={cx(ci)}
          y={mt + plotH + 14}
          textAnchor="middle"
          className="fill-[#5f6b7c] text-[9px]"
        >
          {truncate(category, Math.max(6, Math.floor(band / 7)))}
        </text>
      ))}

      {/* 축 제목 */}
      <text
        x={ml + plotW / 2}
        y={H - 4}
        textAnchor="middle"
        className="fill-[#8f99a8] text-[10px] font-medium"
      >
        {xLabel}
      </text>
      <text
        transform={`translate(11 ${mt + plotH / 2}) rotate(-90)`}
        textAnchor="middle"
        className="fill-[#8f99a8] text-[10px] font-medium"
      >
        {yLabel}
      </text>
    </g>
  );
}

function HorizontalBars({
  categories,
  series,
  matrix,
  niceMax,
  ticks,
  xLabel,
}: {
  categories: string[];
  series: string[];
  matrix: number[][];
  niceMax: number;
  ticks: number[];
  xLabel: string;
}) {
  const ml = 108;
  const mr = 30;
  const mt = 10;
  const mb = 34;
  const plotW = W - ml - mr;
  const plotH = H - mt - mb;
  const xFor = (value: number) => ml + (value / niceMax) * plotW;
  const band = plotH / categories.length;

  return (
    <g>
      {ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={xFor(tick)}
            y1={mt}
            x2={xFor(tick)}
            y2={mt + plotH}
            stroke="#eef1f4"
          />
          <text
            x={xFor(tick)}
            y={mt + plotH + 12}
            textAnchor="middle"
            className="fill-[#8f99a8] text-[9px]"
          >
            {formatMetricValue(tick)}
          </text>
        </g>
      ))}
      <line x1={ml} y1={mt} x2={ml} y2={mt + plotH} stroke="#c5ccd3" />
      <line
        x1={ml}
        y1={mt + plotH}
        x2={ml + plotW}
        y2={mt + plotH}
        stroke="#c5ccd3"
      />

      {categories.map((category, ci) => {
        const barH = Math.min(28, band * 0.6);
        const y = mt + band * ci + (band - barH) / 2;
        let cursorX = ml;
        return (
          <g key={category}>
            {series.map((_, si) => {
              const value = matrix[ci][si];
              const w = (value / niceMax) * plotW;
              const rect =
                w > 0 ? (
                  <g key={si}>
                    <rect
                      x={cursorX}
                      y={y}
                      width={w}
                      height={barH}
                      fill={colorForIndex(si)}
                    />
                    {w > 18 ? (
                      <text
                        x={cursorX + w / 2}
                        y={y + barH / 2 + 3}
                        textAnchor="middle"
                        className="fill-white text-[9px] font-semibold"
                      >
                        {formatMetricValue(value)}
                      </text>
                    ) : null}
                  </g>
                ) : null;
              cursorX += w;
              return rect;
            })}
            <text
              x={cursorX + 4}
              y={y + barH / 2 + 3}
              className="fill-[#404854] text-[9px] font-semibold"
            >
              {formatMetricValue(sum(matrix[ci]))}
            </text>
            <text
              x={ml - 6}
              y={y + barH / 2 + 3}
              textAnchor="end"
              className="fill-[#5f6b7c] text-[9px]"
            >
              {truncate(category, 16)}
            </text>
          </g>
        );
      })}

      <text
        x={ml + plotW / 2}
        y={H - 2}
        textAnchor="middle"
        className="fill-[#8f99a8] text-[10px] font-medium"
      >
        {xLabel}
      </text>
    </g>
  );
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function sumUpTo(values: number[], index: number): number {
  let total = 0;
  for (let i = 0; i <= index && i < values.length; i += 1) total += values[i];
  return total;
}

function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  const normalized = value / magnitude;
  const nice =
    normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return nice * magnitude;
}

function axisTicks(max: number, count: number): number[] {
  const step = max / count;
  return Array.from(
    { length: count + 1 },
    (_, index) => Math.round(step * index * 100) / 100,
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
