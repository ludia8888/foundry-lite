import type { GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import { ChevronDown, ChevronRight, Search, X } from "lucide-react";
import { useState } from "react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  businessPropertyName,
  businessStatus,
} from "../../lib/business-display";
import { isNumericType } from "../../lib/ontology-context";
import {
  facetCounts,
  isFilterActive,
  numericExtent,
  selectedRange,
  selectedValues,
  useRuntimeDispatch,
  useRuntimeState,
  type RuntimeDispatch,
  type RuntimeState,
} from "../../lib/runtime-state";
import {
  objectViewFor,
  useWidgetObjects,
  WidgetFrame,
  WidgetPlaceholder,
  type WidgetRuntimeProps,
} from "./widget-kit";
import { useWorkshopRuntimeDefinition } from "../runtime-application-context";

const MISSING_OBJECT = (
  <WidgetPlaceholder
    label="연결할 업무가 필요합니다"
    hint="AI FDE에게 이 화면에서 찾고 싶은 업무를 설명해 주세요."
  />
);

type FilterKind = "histogram" | "typeahead" | "range";

/** 속성별 필터 표시 유형을 데이터 타입·카디널리티로 결정 (Palantir 기본 규칙). */
function filterKindFor(
  property: string,
  objectView: FoundryLiteOntologyObjectView | null,
  allObjects: readonly GenericObject[],
): FilterKind {
  const view = objectView?.properties.find((p) => p.apiName === property);
  if (view && isNumericType(view.dataType)) return "range";
  const distinct = facetCounts(allObjects, property).length;
  return distinct <= 12 ? "histogram" : "typeahead";
}

/** 필터 대상 패싯 속성: config 지정 → 저카디널리티 문자열 자동 도출. */
function facetProperties(
  configured: string[] | undefined,
  objectView: FoundryLiteOntologyObjectView | null,
  allObjects: readonly GenericObject[],
): string[] {
  if (configured && configured.length > 0) return configured;
  const hasStatus = allObjects.some((object) => "status" in object.properties);
  const candidates = (objectView?.properties ?? [])
    .filter(
      (property) =>
        property.dataType === "string" &&
        !property.isPrimaryKey &&
        property.apiName !== "status",
    )
    .filter((property) => {
      const distinct = facetCounts(allObjects, property.apiName).length;
      return distinct > 1 && distinct <= 8;
    })
    .map((property) => property.apiName);
  const derived = hasStatus ? ["status", ...candidates] : candidates;
  return derived.slice(0, 4);
}

/**
 * filterList (Palantir Filter List 클론): 상단 키워드 검색 + 속성별 필터 섹션.
 * 속성마다 히스토그램·타입어헤드·숫자 범위 중 데이터에 맞는 유형을 렌더링하고,
 * 선택 카운트·개별 해제·펼침접힘·전체 해제를 제공한다.
 */
export function FilterListWidget(props: WidgetRuntimeProps) {
  const { config } = props.widget;
  const objectApiName = config.objectApiName ?? null;
  const { allObjects } = useWidgetObjects(objectApiName);
  const objectView = objectViewFor(props, objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const definition = useWorkshopRuntimeDefinition();
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

  if (!objectApiName) return MISSING_OBJECT;

  const properties = facetProperties(
    config.propertyApiNames,
    objectView,
    allObjects,
  );
  const activeCount = properties.filter((property) =>
    isFilterActive(state, property),
  ).length;

  const toggleCollapse = (property: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(property)) next.delete(property);
      else next.add(property);
      return next;
    });

  return (
    <WidgetFrame
      title={config.title || "필터"}
      subtitle={activeCount > 0 ? `${activeCount}개 적용` : undefined}
      actions={
        activeCount > 0 ? (
          <button
            type="button"
            className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium text-[#2d72d2] hover:bg-[#e8f0fb]"
            onClick={() => dispatch({ type: "clearAllFilters" })}
          >
            전체 해제
          </button>
        ) : null
      }
      bodyClassName="overflow-auto"
    >
      {/* 키워드 검색 */}
      <div className="border-b border-[#eef1f4] p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-[#8f99a8]" />
          <Input
            className="h-8 pl-8 text-[12px]"
            value={state.searchText}
            onChange={(event) =>
              dispatch({ type: "setSearch", text: event.target.value })
            }
            placeholder="키워드 검색..."
          />
          {state.searchText ? (
            <button
              type="button"
              aria-label="검색 지우기"
              className="absolute top-1/2 right-2 flex size-4 -translate-y-1/2 items-center justify-center rounded text-[#8f99a8] hover:bg-[#e4e9ed]"
              onClick={() => dispatch({ type: "setSearch", text: "" })}
            >
              <X className="size-3" />
            </button>
          ) : null}
        </div>
      </div>

      {properties.length === 0 ? (
        <p className="px-3 py-3 text-[11px] text-[#8f99a8]">
          지금 사용할 수 있는 검색 기준이 없습니다.
        </p>
      ) : (
        <div className="divide-y divide-[#eef1f4]">
          {properties.map((property) => (
            <FilterSection
              key={property}
              property={property}
              kind={filterKindFor(property, objectView, allObjects)}
              allObjects={allObjects}
              state={state}
              dispatch={dispatch}
              isCollapsed={collapsed.has(property)}
              onToggleCollapse={() => toggleCollapse(property)}
              label={businessPropertyName(
                objectApiName,
                property,
                objectView,
                definition.presentation,
              )}
            />
          ))}
        </div>
      )}
    </WidgetFrame>
  );
}

function FilterSection({
  property,
  kind,
  allObjects,
  state,
  dispatch,
  isCollapsed,
  onToggleCollapse,
  label,
}: {
  property: string;
  kind: FilterKind;
  allObjects: readonly GenericObject[];
  state: RuntimeState;
  dispatch: RuntimeDispatch;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  label: string;
}) {
  const active = isFilterActive(state, property);
  const selectedCount = selectedValues(state, property).length;

  return (
    <div className="px-2 py-1.5">
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
        >
          {isCollapsed ? (
            <ChevronRight className="size-3 shrink-0 text-[#8f99a8]" />
          ) : (
            <ChevronDown className="size-3 shrink-0 text-[#8f99a8]" />
          )}
          <span className="text-[10px] font-semibold tracking-wide text-[#5f6b7c] uppercase">
            {label}
          </span>
          {selectedCount > 0 ? (
            <span className="rounded-full bg-[#2d72d2] px-1.5 text-[9px] font-bold text-white">
              {selectedCount}
            </span>
          ) : null}
        </button>
        {active ? (
          <button
            type="button"
            aria-label={`${label} 조건 해제`}
            className="flex size-4 items-center justify-center rounded text-[#8f99a8] hover:bg-[#e4e9ed] hover:text-[#cd4246]"
            onClick={() => dispatch({ type: "clearFilter", property })}
          >
            <X className="size-3" />
          </button>
        ) : null}
      </div>
      {!isCollapsed ? (
        <div className="mt-1 pl-4">
          {kind === "range" ? (
            <RangeFilter
              property={property}
              allObjects={allObjects}
              state={state}
              dispatch={dispatch}
            />
          ) : kind === "typeahead" ? (
            <TypeaheadFilter
              property={property}
              label={label}
              allObjects={allObjects}
              state={state}
              dispatch={dispatch}
            />
          ) : (
            <HistogramFilter
              property={property}
              allObjects={allObjects}
              state={state}
              dispatch={dispatch}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}

/** 히스토그램 필터: 값별 카운트 막대 + 체크박스 다중선택. */
function HistogramFilter({
  property,
  allObjects,
  state,
  dispatch,
}: {
  property: string;
  allObjects: readonly GenericObject[];
  state: RuntimeState;
  dispatch: RuntimeDispatch;
}) {
  const definition = useWorkshopRuntimeDefinition();
  const rows = facetCounts(allObjects, property);
  const maxCount = rows.reduce((max, row) => Math.max(max, row.count), 0);
  const selected = selectedValues(state, property);
  return (
    <div className="space-y-0.5">
      {rows.map((row) => {
        const isSelected = selected.includes(row.value);
        const barWidth =
          maxCount > 0 ? Math.round((row.count / maxCount) * 100) : 0;
        return (
          <button
            key={row.value}
            type="button"
            className={cn(
              "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-[#f6f8fa]",
              isSelected && "bg-[#e8f0fb] hover:bg-[#e8f0fb]",
            )}
            onClick={() =>
              dispatch({ type: "toggleFilter", property, value: row.value })
            }
          >
            <CheckSquare checked={isSelected} />
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              {property === "status" ? (
                <span className="min-w-0 truncate">
                  <StatusPill intent={businessStatus(row.value, definition.presentation).intent}>
                    {businessStatus(row.value, definition.presentation).label}
                  </StatusPill>
                </span>
              ) : (
                <span className="min-w-0 truncate text-[12px] text-[#1c2127]">
                  {row.value}
                </span>
              )}
              <span
                className="h-[3px] rounded-full bg-[#2d72d2]/40"
                style={{ width: `${barWidth}%` }}
              />
            </span>
            <span className="shrink-0 text-[11px] text-[#5f6b7c] tabular-nums">
              {row.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** 타입어헤드 필터: 검색 입력으로 값 목록을 좁혀 다중선택. */
function TypeaheadFilter({
  property,
  label,
  allObjects,
  state,
  dispatch,
}: {
  property: string;
  label: string;
  allObjects: readonly GenericObject[];
  state: RuntimeState;
  dispatch: RuntimeDispatch;
}) {
  const definition = useWorkshopRuntimeDefinition();
  const [query, setQuery] = useState("");
  const rows = facetCounts(allObjects, property);
  const selected = selectedValues(state, property);
  const filtered = rows.filter((row) =>
    row.value.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <div className="space-y-1">
      <Input
        className="h-7 text-[12px]"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={`${label} 검색...`}
      />
      <div className="max-h-40 space-y-0.5 overflow-auto">
        {filtered.slice(0, 50).map((row) => {
          const isSelected = selected.includes(row.value);
          return (
            <button
              key={row.value}
              type="button"
              className={cn(
                "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-[#f6f8fa]",
                isSelected && "bg-[#e8f0fb] hover:bg-[#e8f0fb]",
              )}
              onClick={() =>
                dispatch({ type: "toggleFilter", property, value: row.value })
              }
            >
              <CheckSquare checked={isSelected} />
              <span className="min-w-0 flex-1 truncate text-[12px] text-[#1c2127]">
                {property === "status" ? businessStatus(row.value, definition.presentation).label : row.value}
              </span>
              <span className="shrink-0 text-[11px] text-[#5f6b7c] tabular-nums">
                {row.count}
              </span>
            </button>
          );
        })}
        {filtered.length === 0 ? (
          <p className="px-1.5 py-1 text-[11px] text-[#8f99a8]">
            일치하는 값이 없습니다.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** 숫자 범위 필터: 분포 미니 히스토그램 + min·max 입력. */
function RangeFilter({
  property,
  allObjects,
  state,
  dispatch,
}: {
  property: string;
  allObjects: readonly GenericObject[];
  state: RuntimeState;
  dispatch: RuntimeDispatch;
}) {
  const extent = numericExtent(allObjects, property);
  const range = selectedRange(state, property);
  if (!extent) {
    return <p className="text-[11px] text-[#8f99a8]">숫자 값이 없습니다.</p>;
  }
  const buckets = numericBuckets(allObjects, property, extent, 12);
  const maxBucket = Math.max(...buckets, 1);
  const setRange = (min: number | null, max: number | null) =>
    dispatch({ type: "setRange", property, min, max });

  return (
    <div className="space-y-1.5">
      <div className="flex h-10 items-end gap-[2px]">
        {buckets.map((count, index) => (
          <span
            key={index}
            className="flex-1 rounded-t-[1px] bg-[#2d72d2]/50"
            style={{ height: `${Math.round((count / maxBucket) * 100)}%` }}
            title={`${count}건`}
          />
        ))}
      </div>
      <div className="flex items-center gap-1.5">
        <Input
          type="number"
          className="h-7 text-[12px]"
          value={range?.min ?? ""}
          placeholder={String(round(extent.min))}
          onChange={(event) =>
            setRange(
              event.target.value === "" ? null : Number(event.target.value),
              range?.max ?? null,
            )
          }
        />
        <span className="text-[11px] text-[#8f99a8]">–</span>
        <Input
          type="number"
          className="h-7 text-[12px]"
          value={range?.max ?? ""}
          placeholder={String(round(extent.max))}
          onChange={(event) =>
            setRange(
              range?.min ?? null,
              event.target.value === "" ? null : Number(event.target.value),
            )
          }
        />
      </div>
    </div>
  );
}

function CheckSquare({ checked }: { checked: boolean }) {
  return (
    <span
      className={cn(
        "flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border",
        checked ? "border-[#2d72d2] bg-[#2d72d2]" : "border-[#8f99a8] bg-white",
      )}
    >
      {checked ? <span className="size-1.5 rounded-[1px] bg-white" /> : null}
    </span>
  );
}

function numericBuckets(
  objects: readonly GenericObject[],
  property: string,
  extent: { min: number; max: number },
  bucketCount: number,
): number[] {
  const buckets = new Array<number>(bucketCount).fill(0);
  const span = extent.max - extent.min || 1;
  for (const object of objects) {
    const value = object.properties[property];
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    const ratio = (value - extent.min) / span;
    const index = Math.min(bucketCount - 1, Math.floor(ratio * bucketCount));
    buckets[index] += 1;
  }
  return buckets;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * objectDropdown: 단일 속성의 값을 드롭다운으로 선택해 공유 필터를 설정한다.
 */
export function ObjectDropdownWidget(props: WidgetRuntimeProps) {
  const { config } = props.widget;
  const objectApiName = config.objectApiName ?? null;
  const { allObjects } = useWidgetObjects(objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const definition = useWorkshopRuntimeDefinition();

  if (!objectApiName) return MISSING_OBJECT;

  const property = config.filterProperty ?? null;
  if (!property) {
    return (
      <WidgetPlaceholder
        label="검색 기준이 필요합니다"
        hint="AI FDE에게 사용자가 어떤 기준으로 업무를 찾을지 설명해 주세요."
      />
    );
  }

  const distinctValues = facetCounts(allObjects, property);
  const setsVar = config.setsVariableId ?? null;
  const variableValue = setsVar ? state.variables[setsVar] : null;
  const current = setsVar
    ? variableValue !== null &&
      variableValue !== undefined &&
      variableValue !== ""
      ? String(variableValue)
      : "__all__"
    : (selectedValues(state, property)[0] ?? "__all__");

  const handleChange = (value: string) => {
    if (setsVar) {
      dispatch({
        type: "setVariable",
        id: setsVar,
        value: value === "__all__" ? null : value,
      });
      return;
    }
    if (value === "__all__") dispatch({ type: "clearFilter", property });
    else dispatch({ type: "setFilter", property, values: [value] });
  };

  return (
    <WidgetFrame borderless bodyClassName="p-2 space-y-1">
      <p className="px-0.5 text-[10px] text-[#8f99a8]">
        {config.title ||
          businessPropertyName(
            objectApiName,
            property,
            objectViewFor(props, objectApiName),
            definition.presentation,
          )}
      </p>
      <Select value={current} onValueChange={handleChange}>
        <SelectTrigger className="h-8 text-[12px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">전체</SelectItem>
          {distinctValues.map((row) => (
            <SelectItem key={row.value} value={row.value}>
              {property === "status" ? businessStatus(row.value, definition.presentation).label : row.value} ({row.count})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </WidgetFrame>
  );
}

/**
 * searchBar: 페이지 전역 키워드 검색. 객체 타입에 무관하게 모든 위젯을 필터링한다.
 */
export function SearchBarWidget(props: WidgetRuntimeProps) {
  const { config } = props.widget;
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();

  return (
    <WidgetFrame borderless bodyClassName="p-2">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-[#8f99a8]" />
        <Input
          className="h-8 pl-8 text-[12px]"
          value={state.searchText}
          onChange={(event) =>
            dispatch({ type: "setSearch", text: event.target.value })
          }
          placeholder={config.title || "검색..."}
        />
        {state.searchText ? (
          <button
            type="button"
            className="absolute top-1/2 right-2 flex size-4 -translate-y-1/2 items-center justify-center rounded text-[#8f99a8] hover:bg-[#e4e9ed] hover:text-[#1c2127]"
            onClick={() => dispatch({ type: "setSearch", text: "" })}
          >
            <X className="size-3" />
          </button>
        ) : null}
      </div>
    </WidgetFrame>
  );
}

/**
 * stringSelector: 단일 속성 값을 토글 칩으로 다중 선택하는 필터.
 */
export function StringSelectorWidget(props: WidgetRuntimeProps) {
  const { config } = props.widget;
  const objectApiName = config.objectApiName ?? null;
  const { allObjects } = useWidgetObjects(objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const definition = useWorkshopRuntimeDefinition();

  if (!objectApiName) return MISSING_OBJECT;

  const property = config.filterProperty ?? null;
  if (!property) {
    return (
      <WidgetPlaceholder
        label="검색 기준이 필요합니다"
        hint="AI FDE에게 사용자가 어떤 기준으로 업무를 찾을지 설명해 주세요."
      />
    );
  }

  const values = facetCounts(allObjects, property);
  const selected = selectedValues(state, property);

  return (
    <WidgetFrame
      title={
        config.title ||
        businessPropertyName(
          objectApiName,
          property,
          objectViewFor(props, objectApiName),
          definition.presentation,
        )
      }
      bodyClassName="p-2"
    >
      <div className="flex flex-wrap gap-1.5">
        {values.map((row) => {
          const isSelected = selected.includes(row.value);
          return (
            <button
              key={row.value}
              type="button"
              className={cn(
                "flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px]",
                isSelected
                  ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                  : "border-[#d5dce1] text-[#1c2127] hover:bg-[#f6f8fa]",
              )}
              onClick={() =>
                dispatch({ type: "toggleFilter", property, value: row.value })
              }
            >
              <span className="truncate">{property === "status" ? businessStatus(row.value, definition.presentation).label : row.value}</span>
              <span className="text-[#8f99a8] tabular-nums">{row.count}</span>
            </button>
          );
        })}
      </div>
    </WidgetFrame>
  );
}
