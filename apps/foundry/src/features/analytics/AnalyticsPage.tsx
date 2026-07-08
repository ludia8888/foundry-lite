import type {
  DatasetAggregateOperator,
  DatasetAggregateRequest,
  DatasetAggregationResult,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedDatasetAggregate,
  useFoundryLiteProvidedDatasetExplorer,
} from "@foundry-lite/sdk/react";
import { FlaskConical, Info, Layers, Waypoints } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { AggregateBoard } from "./AggregateBoard";
import { FilterBoard } from "./FilterBoard";
import { ObjectAggregateCard } from "./ObjectAggregateCard";
import { PathConnector } from "./PathBoard";
import { SummaryBoard } from "./SummaryBoard";
import { TableBoard } from "./TableBoard";
import type {
  AggregateFunction,
  AggregateResult,
  FilterCondition,
  PreviewColumn,
} from "./analytics-model";
import {
  applyFilters,
  computeAggregate,
  derivePreviewColumns,
  formatCellValue,
  pickDefaultGroupByColumn,
} from "./analytics-model";

const PREVIEW_LIMIT = 50;
const DEFAULT_DATASET = { namespace: "clean", name: "orders" };

const FUTURE_GAP_ITEMS = [
  "Quiver/Contour parity (notebook·pivot·vega)",
  "notebook compute runtime",
  "spreadsheet collaboration",
  "analysis 저장/공유 · 대시보드 배포",
];

let conditionSeq = 0;
function nextConditionId(): string {
  conditionSeq += 1;
  return `cond-${conditionSeq}`;
}

function pickDefaultMetric(columns: readonly PreviewColumn[]): string {
  const numeric = columns.find(
    (column) => !column.isSystem && column.isNumeric,
  );
  return numeric?.name ?? "";
}

/**
 * Analytics: Contour식 분석 경로 보드 + Quiver식 오브젝트 집계 카드.
 * 데이터셋 선택 → 프리뷰(TABLE) → 필터(FILTER) → 집계(DISTRIBUTION) → 요약(SUMMARY)
 * 를 실데이터(datasets.preview · objects.aggregate)로 완주한다.
 * reference_only 화면: 전체 analytics suite는 future_gap.
 */
export default function AnalyticsPage() {
  const [selectedRef, setSelectedRef] = useState(
    `${DEFAULT_DATASET.namespace}.${DEFAULT_DATASET.name}`,
  );
  const [namespace, name] = selectedRef.split(".", 2);

  const explorer = useFoundryLiteProvidedDatasetExplorer({
    namespace,
    name,
    previewLimit: PREVIEW_LIMIT,
  });

  useEffect(() => {
    if (explorer.datasets.length === 0) return;
    const selectedExists = explorer.datasets.some(
      (dataset) => `${dataset.namespace}.${dataset.name}` === selectedRef,
    );
    if (selectedExists) return;
    const firstDataset = explorer.datasets[0];
    setSelectedRef(`${firstDataset.namespace}.${firstDataset.name}`);
  }, [explorer.datasets, selectedRef]);

  const previewRows = explorer.previewRows;
  const columns = useMemo(
    () => derivePreviewColumns(previewRows),
    [previewRows],
  );

  const [shouldShowSystemColumns, setShouldShowSystemColumns] = useState(false);
  const [conditions, setConditions] = useState<readonly FilterCondition[]>([]);
  const [groupBy, setGroupBy] = useState("");
  const [aggregateFn, setAggregateFn] = useState<AggregateFunction>("count");
  const [metricColumn, setMetricColumn] = useState("");

  // 데이터셋 변경 시 필터/집계 컬럼 선택을 새 스키마에 맞춰 초기화한다.
  useEffect(() => {
    setConditions([]);
    setGroupBy(pickDefaultGroupByColumn(previewRows, columns));
    setMetricColumn(pickDefaultMetric(columns));
    setAggregateFn("count");
  }, [previewRows, columns]);

  const filteredRows = useMemo(
    () => applyFilters(previewRows, conditions),
    [previewRows, conditions],
  );

  const clientAggregateResult = useMemo(
    () => computeAggregate(filteredRows, groupBy, aggregateFn, metricColumn),
    [filteredRows, groupBy, aggregateFn, metricColumn],
  );

  const dataColumnCount = columns.filter((column) => !column.isSystem).length;
  const versionId = explorer.inspectedVersion?.id ?? null;

  const datasetAggregateRequest = useMemo<DatasetAggregateRequest | null>(
    () =>
      buildDatasetAggregateRequest({
        aggregateFn,
        conditions,
        groupBy,
        metricColumn,
        versionId,
      }),
    [aggregateFn, conditions, groupBy, metricColumn, versionId],
  );
  const datasetAggregate = useFoundryLiteProvidedDatasetAggregate(
    { namespace, name },
    datasetAggregateRequest,
    {
      enabled: explorer.hasPreviewRows && datasetAggregateRequest !== null,
    },
  );
  const serverAggregateResult = useMemo(
    () => datasetAggregationToDistribution(datasetAggregate.data, groupBy),
    [datasetAggregate.data, groupBy],
  );
  const aggregateResult = serverAggregateResult ?? clientAggregateResult;

  const handleAddCondition = () => {
    const firstColumn = columns.find((column) => !column.isSystem)?.name;
    if (!firstColumn) return;
    setConditions((previous) => [
      ...previous,
      {
        id: nextConditionId(),
        column: firstColumn,
        operator: "eq",
        value: "",
      },
    ]);
  };

  const handleUpdateCondition = (
    id: string,
    patch: Partial<FilterCondition>,
  ) => {
    setConditions((previous) =>
      previous.map((condition) =>
        condition.id === id ? { ...condition, ...patch } : condition,
      ),
    );
  };

  const handleRemoveCondition = (id: string) => {
    setConditions((previous) =>
      previous.filter((condition) => condition.id !== id),
    );
  };

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        title="Analytics"
        description="Contour 분석 경로 보드 + Quiver 오브젝트 집계 (reference_only)"
        meta={<StatusPill intent="neutral">P3 · reference_only</StatusPill>}
      />

      <ReferenceBanner />

      <div className="flex flex-wrap items-center gap-2 rounded border bg-card px-3 py-2">
        <Layers className="size-4 text-primary" />
        <span className="section-label">데이터셋</span>
        <Select value={selectedRef} onValueChange={setSelectedRef}>
          <SelectTrigger size="sm" className="h-8 w-full min-w-0 text-[12px] sm:w-64">
            <SelectValue placeholder="데이터셋 선택" />
          </SelectTrigger>
          <SelectContent>
            {explorer.datasets.map((dataset) => (
              <SelectItem
                key={dataset.id}
                value={`${dataset.namespace}.${dataset.name}`}
                className="text-[12px]"
              >
                {dataset.namespace}.{dataset.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="font-mono text-[11px] text-muted-foreground">
          datasets.list · datasets.preview · datasets.aggregate
        </span>
        {explorer.hasDatasetSelection ? (
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            {filteredRows.length}/{previewRows.length}행 · {dataColumnCount}컬럼
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div>
          {explorer.isLoading && previewRows.length === 0 ? (
            <LoadingState rowCount={8} />
          ) : explorer.error ? (
            <ErrorState error={explorer.error} onRetry={explorer.reload} />
          ) : previewRows.length === 0 ? (
            <EmptyState
              icon={Waypoints}
              title="프리뷰할 행이 없습니다"
              description="선택한 데이터셋 branch head에 커밋된 데이터가 없습니다. 다른 데이터셋을 선택하세요."
            />
          ) : (
            <div>
              <TableBoard
                datasetRef={selectedRef}
                rows={previewRows}
                columns={columns}
                totalRows={explorer.rowCount ?? previewRows.length}
                shouldShowSystemColumns={shouldShowSystemColumns}
                onToggleSystemColumns={() =>
                  setShouldShowSystemColumns((previous) => !previous)
                }
              />
              <PathConnector />
              <FilterBoard
                columns={columns}
                conditions={conditions}
                keptRows={filteredRows.length}
                totalRows={previewRows.length}
                onAddCondition={handleAddCondition}
                onUpdateCondition={handleUpdateCondition}
                onRemoveCondition={handleRemoveCondition}
              />
              <PathConnector />
              <AggregateBoard
                columns={columns}
                groupBy={groupBy}
                aggregateFn={aggregateFn}
                metricColumn={metricColumn}
                result={aggregateResult}
                serverEvidence={{
                  endpoint: `POST /api/datasets/${namespace}/${name}/aggregate`,
                  filteredRowCount: datasetAggregate.filteredRowCount,
                  rowCount: datasetAggregate.rowCount,
                  status: datasetAggregate.isLoading
                    ? "loading"
                    : datasetAggregate.error
                      ? "error"
                      : datasetAggregate.data
                        ? "ready"
                        : "idle",
                  requestId: datasetAggregate.requestId,
                  errorMessage: datasetAggregate.error?.message ?? null,
                }}
                onGroupByChange={setGroupBy}
                onFunctionChange={setAggregateFn}
                onMetricColumnChange={setMetricColumn}
              />
              <PathConnector />
              <SummaryBoard
                datasetRef={selectedRef}
                previewRows={previewRows.length}
                keptRows={filteredRows.length}
                columnCount={dataColumnCount}
                groupCount={aggregateResult.bars.length}
                versionId={versionId}
              />
            </div>
          )}
        </div>

        <div className="space-y-4">
          <ObjectAggregateCard />
          <FutureSuitePanel />
        </div>
      </div>
    </div>
  );
}

interface BuildDatasetAggregateRequestOptions {
  aggregateFn: AggregateFunction;
  conditions: readonly FilterCondition[];
  groupBy: string;
  metricColumn: string;
  versionId: string | null;
}

function buildDatasetAggregateRequest({
  aggregateFn,
  conditions,
  groupBy,
  metricColumn,
  versionId,
}: BuildDatasetAggregateRequestOptions): DatasetAggregateRequest | null {
  if (!groupBy) return null;
  const needsMetric = aggregateFn !== "count";
  if (needsMetric && !metricColumn) return null;
  return {
    version: versionId ?? undefined,
    filter: conditions
      .filter((condition) => condition.column && condition.value.trim() !== "")
      .map((condition) => ({
        column: condition.column,
        operator: condition.operator as DatasetAggregateOperator,
        value: condition.value,
      })),
    groupBy: [groupBy],
    select: [
      {
        function: aggregateFn,
        name: "value",
        property: needsMetric ? metricColumn : null,
      },
    ],
  };
}

function datasetAggregationToDistribution(
  result: DatasetAggregationResult | null,
  groupBy: string,
): AggregateResult | null {
  if (!result) return null;
  const bars = result.groups
    .map((group) => ({
      key: formatCellValue(group.key[groupBy]),
      value: Number(group.metrics.value ?? 0),
    }))
    .sort((left, right) => right.value - left.value);
  const maxValue = bars.reduce((max, bar) => Math.max(max, bar.value), 0);
  const totalValue = bars.reduce((total, bar) => total + bar.value, 0);
  return { bars, maxValue, totalValue };
}

/** reference_only 배너: 참고 화면 분류 + 재사용 패턴 출처 명시 (acceptance). */
function ReferenceBanner() {
  return (
    <div className="flex items-start gap-2.5 rounded border border-primary/30 bg-primary/5 p-3">
      <Info className="mt-0.5 size-4 shrink-0 text-primary" />
      <div className="min-w-0 space-y-1 text-[12px]">
        <div className="text-[13px] font-semibold text-foreground">
          reference_only — 전체 analytics suite는 future
        </div>
        <p className="text-muted-foreground">
          Contour(분석 경로 보드) · Quiver(집계 차트) 패턴을 실데이터로 참고
          구현합니다. 재사용 패턴 출처:{" "}
          <span className="font-medium text-foreground/80">TABLE</span> →
          Dataset Explorer 프리뷰 문법,{" "}
          <span className="font-medium text-foreground/80">FILTER</span> →
          Contour keep-rows,{" "}
          <span className="font-medium text-foreground/80">DISTRIBUTION</span> →
          Contour distribution board,{" "}
          <span className="font-medium text-foreground/80">QUIVER</span> →
          Object Explorer 서버 집계.
        </p>
      </div>
    </div>
  );
}

/** future suite와 현재 구현을 분리해 명시한다 (acceptance). */
function FutureSuitePanel() {
  return (
    <div className="rounded border bg-card">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-1.5">
        <FlaskConical className="size-3.5 text-muted-foreground" />
        <span className="section-label">Future suite (미구현)</span>
      </div>
      <ul className="divide-y">
        {FUTURE_GAP_ITEMS.map((item) => (
          <li
            key={item}
            className="flex items-center justify-between gap-2 px-3 py-1.5 text-[12px]"
          >
            <span className="min-w-0 truncate text-foreground/80">{item}</span>
            <StatusPill intent="neutral">future</StatusPill>
          </li>
        ))}
      </ul>
      <p className="border-t px-3 py-1.5 text-[11px] text-muted-foreground">
        현재 구현 범위: dataset preview · dataset 서버 집계 · object 서버 집계.
      </p>
    </div>
  );
}
