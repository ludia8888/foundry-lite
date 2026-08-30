import type { GenericObject } from "@foundry-lite/sdk";
import { ChevronLeft, ChevronRight, Route, TableProperties } from "lucide-react";
import { useMemo, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import { formatMetricValue, crossAggregate, metricLabel } from "../../lib/aggregate";
import { formatCellValue, objectTitleOf, statusIntentOf } from "../../lib/app-model";
import { useRuntimeDispatch, useRuntimeState } from "../../lib/runtime-state";
import {
  objectViewFor,
  useWidgetObjects,
  WidgetFrame,
  WidgetPlaceholder,
  type WidgetRuntimeProps,
} from "./widget-kit";

const MISSING_OBJECT = <WidgetPlaceholder label="업무 기록이 연결되지 않았습니다." />;

export function KanbanWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isTruncated } = useWidgetObjects(objectApiName, widget.config.variableFilters);
  const objectView = objectViewFor(props, objectApiName);
  const groupBy = widget.config.groupByProperty;
  const dispatch = useRuntimeDispatch();
  const state = useRuntimeState();
  if (!objectApiName) return MISSING_OBJECT;
  if (!groupBy) return <WidgetPlaceholder label="상태 기준을 정해주세요." hint="업무가 어떤 단계로 나뉘는지 선택하면 보드가 만들어집니다." />;
  const groups = groupObjects(objects, groupBy).slice(0, 8);
  return (
    <WidgetFrame title={widget.config.title || "업무 보드"} subtitle={isTruncated ? "상한 표본" : `${objects.length}건`} bodyClassName="overflow-x-auto p-3">
      <div className="grid min-w-[720px] auto-cols-[minmax(220px,1fr)] grid-flow-col gap-3">
        {groups.map(([status, items]) => (
          <section key={status} className="rounded-xl bg-[var(--workshop-subtle)] p-2.5">
            <div className="mb-2 flex items-center gap-2 px-1">
              <span className="size-2 rounded-full bg-[var(--workshop-accent)]" />
              <h3 className="min-w-0 flex-1 truncate text-[11px] font-semibold text-[var(--workshop-ink)]">{status}</h3>
              <span className="rounded-full bg-white px-1.5 py-0.5 text-[10px] font-semibold text-[#657386]">{items.length}</span>
            </div>
            <div className="space-y-2">
              {items.slice(0, 50).map((object) => (
                <button
                  key={object.objectId}
                  type="button"
                  onClick={() => dispatch({ type: "selectObject", objectId: object.objectId })}
                  className={cn(
                    "w-full rounded-lg border bg-white p-3 text-left shadow-[0_1px_2px_rgba(15,23,42,.05)] transition hover:-translate-y-px hover:border-[var(--workshop-accent)]",
                    state.selectedObjectId === object.objectId ? "border-[var(--workshop-accent)] ring-2 ring-[var(--workshop-accent-soft)]" : "border-[var(--workshop-line)]",
                  )}
                >
                  <strong className="block truncate text-[12px] text-[var(--workshop-ink)]">{objectTitleOf(object, objectView)}</strong>
                  <CardProperties object={object} properties={widget.config.propertyApiNames} excluded={groupBy} />
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>
    </WidgetFrame>
  );
}

function CardProperties({ object, properties, excluded }: { object: GenericObject; properties?: string[]; excluded: string }) {
  const selected = (properties?.length ? properties : Object.keys(object.properties))
    .filter((name) => name !== excluded && object.properties[name] !== undefined)
    .slice(0, 2);
  return (
    <div className="mt-2 space-y-1">
      {selected.map((name) => (
        <div key={name} className="flex gap-2 text-[10px] text-[#748195]">
          <span className="min-w-0 flex-1 truncate">{name}</span>
          <span className="max-w-[55%] truncate font-medium text-[#465468]">{formatCellValue(object.properties[name])}</span>
        </div>
      ))}
    </div>
  );
}

export function StatusTrackerWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects } = useWidgetObjects(objectApiName, widget.config.variableFilters);
  const groupBy = widget.config.groupByProperty;
  if (!objectApiName) return MISSING_OBJECT;
  if (!groupBy) return <WidgetPlaceholder label="추적할 상태 기준을 정해주세요." />;
  const groups = groupObjects(objects, groupBy);
  return (
    <WidgetFrame title={widget.config.title || "업무 흐름"} actions={<Route className="size-4 text-[var(--workshop-accent)]" />} bodyClassName="p-3">
      <ol className="grid gap-2 md:grid-cols-[repeat(auto-fit,minmax(120px,1fr))]">
        {groups.map(([status, items], index) => (
          <li key={status} className="relative rounded-lg border border-[var(--workshop-line)] bg-white p-3">
            <div className="flex items-center gap-2">
              <span className="flex size-5 items-center justify-center rounded-full bg-[var(--workshop-accent-soft)] text-[10px] font-bold text-[var(--workshop-accent)]">{index + 1}</span>
              <StatusPill intent={statusIntentOf(status)}>{status}</StatusPill>
            </div>
            <div className="mt-2 text-[22px] font-semibold tracking-tight text-[var(--workshop-ink)]">{items.length}<span className="ml-1 text-[11px] font-normal text-[#748195]">건</span></div>
          </li>
        ))}
      </ol>
    </WidgetFrame>
  );
}

export function CalendarWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects } = useWidgetObjects(objectApiName, widget.config.variableFilters);
  const dateProperty = widget.config.dateProperty;
  const objectView = objectViewFor(props, objectApiName);
  const dated = useMemo(() => datedObjects(objects, dateProperty), [dateProperty, objects]);
  const [selectedMonth, setSelectedMonth] = useState<Date | null>(null);
  const visibleMonth = selectedMonth ?? monthStart(dated[0]?.date ?? new Date());
  const dispatch = useRuntimeDispatch();
  if (!objectApiName) return MISSING_OBJECT;
  if (!dateProperty) return <WidgetPlaceholder label="일정 날짜를 정해주세요." hint="예약일, 마감일, 방문일 같은 정보를 선택하면 캘린더가 만들어집니다." />;
  const days = calendarDays(visibleMonth);
  return (
    <WidgetFrame
      title={widget.config.title || "업무 캘린더"}
      actions={<CalendarControls month={visibleMonth} onChange={setSelectedMonth} />}
      bodyClassName="overflow-auto p-3"
    >
      <div className="min-w-[680px]">
        <div className="grid grid-cols-7 text-center text-[10px] font-semibold text-[#748195]">{["일", "월", "화", "수", "목", "금", "토"].map((day) => <div key={day} className="py-1.5">{day}</div>)}</div>
        <div className="grid grid-cols-7 overflow-hidden rounded-xl border border-[var(--workshop-line)] bg-[var(--workshop-line)] gap-px">
          {days.map((day) => {
            const items = dated.filter((item) => sameDay(item.date, day));
            const isCurrent = day.getMonth() === visibleMonth.getMonth();
            return (
              <div key={day.toISOString()} className={cn("min-h-[92px] bg-white p-2", !isCurrent && "bg-[var(--workshop-subtle)] text-[#a0aaba]")}>
                <div className="text-[10px] font-semibold">{day.getDate()}</div>
                <div className="mt-1 space-y-1">
                  {items.slice(0, 3).map((item) => (
                    <button key={item.object.objectId} type="button" onClick={() => dispatch({ type: "selectObject", objectId: item.object.objectId })} className="block w-full truncate rounded bg-[var(--workshop-accent-soft)] px-1.5 py-1 text-left text-[9px] font-medium text-[var(--workshop-accent)] hover:ring-1 hover:ring-[var(--workshop-accent)]">
                      {objectTitleOf(item.object, objectView)}
                    </button>
                  ))}
                  {items.length > 3 ? <div className="px-1 text-[9px] text-[#748195]">+{items.length - 3}건</div> : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </WidgetFrame>
  );
}

function CalendarControls({ month, onChange }: { month: Date; onChange: (value: Date) => void }) {
  const shift = (delta: number) => onChange(new Date(month.getFullYear(), month.getMonth() + delta, 1));
  return (
    <div className="flex items-center gap-1">
      <button type="button" aria-label="이전 달" onClick={() => shift(-1)} className="rounded p-1 hover:bg-[var(--workshop-subtle)]"><ChevronLeft className="size-3.5" /></button>
      <span className="min-w-20 text-center text-[11px] font-semibold">{month.toLocaleDateString("ko-KR", { year: "numeric", month: "long" })}</span>
      <button type="button" aria-label="다음 달" onClick={() => shift(1)} className="rounded p-1 hover:bg-[var(--workshop-subtle)]"><ChevronRight className="size-3.5" /></button>
    </div>
  );
}

export function PivotTableWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isTruncated } = useWidgetObjects(objectApiName, widget.config.variableFilters);
  const groupBy = widget.config.groupByProperty;
  if (!objectApiName) return MISSING_OBJECT;
  if (!groupBy) return <WidgetPlaceholder label="행으로 비교할 업무 기준을 정해주세요." />;
  const metric = widget.config.metric ?? "count";
  const data = crossAggregate(objects, groupBy, widget.config.seriesProperty, metric, widget.config.metricProperty);
  return (
    <WidgetFrame title={widget.config.title || "업무 피벗"} subtitle={`${metricLabel(metric)}${isTruncated ? " · 상한 표본" : ""}`} actions={<TableProperties className="size-4 text-[var(--workshop-accent)]" />} bodyClassName="overflow-auto">
      <table className="w-full min-w-[520px] border-collapse text-[11px]">
        <thead><tr className="bg-[var(--workshop-subtle)] text-[#657386]"><th className="sticky left-0 bg-[var(--workshop-subtle)] px-3 py-2 text-left">{groupBy}</th>{data.series.map((series) => <th key={series} className="px-3 py-2 text-right">{series}</th>)}<th className="px-3 py-2 text-right">합계</th></tr></thead>
        <tbody>{data.categories.map((category, rowIndex) => <tr key={category} className="border-t border-[var(--workshop-line)]"><th className="sticky left-0 bg-white px-3 py-2 text-left font-medium text-[var(--workshop-ink)]">{category}</th>{data.matrix[rowIndex].map((value, columnIndex) => <td key={`${category}-${data.series[columnIndex]}`} className="px-3 py-2 text-right tabular-nums text-[#465468]">{formatMetricValue(value)}</td>)}<td className="px-3 py-2 text-right font-semibold tabular-nums text-[var(--workshop-ink)]">{formatMetricValue(data.categoryTotals[rowIndex])}</td></tr>)}</tbody>
      </table>
    </WidgetFrame>
  );
}

function groupObjects(objects: readonly GenericObject[], property: string): Array<[string, GenericObject[]]> {
  const groups = new Map<string, GenericObject[]>();
  for (const object of objects) {
    const value = String(object.properties[property] ?? "미지정");
    const group = groups.get(value);
    if (group) group.push(object);
    else groups.set(value, [object]);
  }
  return Array.from(groups.entries());
}

function datedObjects(objects: readonly GenericObject[], property?: string | null) {
  if (!property) return [];
  return objects.flatMap((object) => {
    const date = new Date(String(object.properties[property] ?? ""));
    return Number.isNaN(date.getTime()) ? [] : [{ object, date }];
  }).sort((a, b) => a.date.getTime() - b.date.getTime());
}

function monthStart(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function calendarDays(month: Date): Date[] {
  const start = new Date(month.getFullYear(), month.getMonth(), 1 - month.getDay());
  return Array.from({ length: 42 }, (_, index) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + index));
}

function sameDay(left: Date, right: Date): boolean {
  return left.getFullYear() === right.getFullYear() && left.getMonth() === right.getMonth() && left.getDate() === right.getDate();
}
