import type { GenericObject } from "@foundry-lite/sdk";
import {
  createContext,
  useContext,
  useMemo,
  useReducer,
  type Dispatch,
} from "react";

/**
 * 속성별 필터. 값-집합(문자열 히스토그램·드롭다운·칩)과 숫자 범위(range)를 지원한다.
 * Palantir Filter List의 필터 유형(histogram/multi-select/range)에 대응.
 */
export type RuntimeFilter =
  | { kind: "values"; values: readonly string[] }
  | { kind: "range"; min: number | null; max: number | null };

/**
 * 페이지 런타임 공유 상태. 필터/검색/선택을 위젯 간에 공유해
 * "필터 위젯이 테이블·차트를 실제로 필터링하고, 행 선택이 상세·액션을 구동하는"
 * Palantir식 인터랙션을 만든다.
 */
export type RuntimeState = {
  filters: Record<string, RuntimeFilter>;
  searchText: string;
  selectedObjectId: string | null;
  dataVersion: number;
  /** 현재 열린 오버레이 id (드로어·모달). */
  openOverlayId: string | null;
  /** 선언된 앱 변수의 현재 값 (변수 id → 값). 위젯 간 명시적 배선. */
  variables: Record<string, unknown>;
};

export type RuntimeAction =
  | { type: "toggleFilter"; property: string; value: string }
  | { type: "setFilter"; property: string; values: readonly string[] }
  | {
      type: "setRange";
      property: string;
      min: number | null;
      max: number | null;
    }
  | { type: "clearFilter"; property: string }
  | { type: "clearAllFilters" }
  | { type: "setSearch"; text: string }
  | { type: "selectObject"; objectId: string | null }
  | { type: "openOverlay"; overlayId: string }
  | { type: "closeOverlay" }
  | { type: "setVariable"; id: string; value: unknown }
  | { type: "bumpData" };

export type RuntimeDispatch = Dispatch<RuntimeAction>;

export function initialRuntimeState(
  variables: Record<string, unknown> = {},
): RuntimeState {
  return {
    filters: {},
    searchText: "",
    selectedObjectId: null,
    dataVersion: 0,
    openOverlayId: null,
    variables,
  };
}

/** 값-집합 필터에서 선택된 값들. 다른 종류거나 없으면 빈 배열. */
export function selectedValues(
  state: RuntimeState,
  property: string,
): readonly string[] {
  const filter = state.filters[property];
  return filter && filter.kind === "values" ? filter.values : [];
}

/** 범위 필터. 다른 종류거나 없으면 null. */
export function selectedRange(
  state: RuntimeState,
  property: string,
): { min: number | null; max: number | null } | null {
  const filter = state.filters[property];
  return filter && filter.kind === "range"
    ? { min: filter.min, max: filter.max }
    : null;
}

export function isFilterActive(state: RuntimeState, property: string): boolean {
  const filter = state.filters[property];
  if (!filter) return false;
  if (filter.kind === "values") return filter.values.length > 0;
  return filter.min !== null || filter.max !== null;
}

export function runtimeReducer(
  state: RuntimeState,
  action: RuntimeAction,
): RuntimeState {
  switch (action.type) {
    case "toggleFilter": {
      const current = selectedValues(state, action.property);
      const next = current.includes(action.value)
        ? current.filter((value) => value !== action.value)
        : [...current, action.value];
      const filters = { ...state.filters };
      if (next.length === 0) delete filters[action.property];
      else filters[action.property] = { kind: "values", values: next };
      return { ...state, filters };
    }
    case "setFilter": {
      const filters = { ...state.filters };
      if (action.values.length === 0) delete filters[action.property];
      else filters[action.property] = { kind: "values", values: action.values };
      return { ...state, filters };
    }
    case "setRange": {
      const filters = { ...state.filters };
      if (action.min === null && action.max === null) {
        delete filters[action.property];
      } else {
        filters[action.property] = {
          kind: "range",
          min: action.min,
          max: action.max,
        };
      }
      return { ...state, filters };
    }
    case "clearFilter": {
      const filters = { ...state.filters };
      delete filters[action.property];
      return { ...state, filters };
    }
    case "clearAllFilters":
      return { ...state, filters: {} };
    case "setSearch":
      return { ...state, searchText: action.text };
    case "selectObject":
      return { ...state, selectedObjectId: action.objectId };
    case "openOverlay":
      return { ...state, openOverlayId: action.overlayId };
    case "closeOverlay":
      return { ...state, openOverlayId: null };
    case "setVariable":
      return {
        ...state,
        variables: { ...state.variables, [action.id]: action.value },
      };
    case "bumpData":
      return { ...state, dataVersion: state.dataVersion + 1 };
    default:
      return state;
  }
}

const RuntimeStateContext = createContext<RuntimeState | null>(null);
const RuntimeDispatchContext = createContext<RuntimeDispatch | null>(null);

export const RuntimeStateProvider = RuntimeStateContext.Provider;
export const RuntimeDispatchProvider = RuntimeDispatchContext.Provider;

export function useRuntimeState(): RuntimeState {
  const state = useContext(RuntimeStateContext);
  if (!state) {
    throw new Error("useRuntimeState must be used within RuntimeStateProvider");
  }
  return state;
}

export function useRuntimeDispatch(): RuntimeDispatch {
  const dispatch = useContext(RuntimeDispatchContext);
  if (!dispatch) {
    throw new Error(
      "useRuntimeDispatch must be used within RuntimeDispatchProvider",
    );
  }
  return dispatch;
}

/** RuntimeMode 최상위에서 상태를 만든다. 변수 기본값으로 초기화한다. */
export function useRuntimeStateReducer(
  initialVariables: Record<string, unknown> = {},
) {
  return useReducer(runtimeReducer, initialVariables, initialRuntimeState);
}

/**
 * 변수 기반 필터를 객체 배열에 적용한다.
 * 각 바인딩은 `property === 변수값`으로 AND 결합하며,
 * 변수값이 비어(null/undefined/"") 있으면 해당 조건을 무시(전체 통과)한다.
 */
export function applyVariableFilters(
  objects: readonly GenericObject[],
  variableFilters:
    readonly { property: string; variableId: string }[] | undefined,
  variables: Record<string, unknown>,
): GenericObject[] {
  if (!variableFilters || variableFilters.length === 0) return [...objects];
  const active = variableFilters
    .map((filter) => ({
      property: filter.property,
      value: variables[filter.variableId],
    }))
    .filter(
      (filter) =>
        filter.value !== null &&
        filter.value !== undefined &&
        filter.value !== "",
    );
  if (active.length === 0) return [...objects];
  return objects.filter((object) =>
    active.every(
      (filter) =>
        String(object.properties[filter.property] ?? "") ===
        String(filter.value),
    ),
  );
}

/**
 * 공유 필터·검색을 객체 배열에 적용한다.
 * - 값-집합 필터: 선택값 중 하나 일치(OR), 속성 간 AND.
 * - 범위 필터: min≤value≤max.
 * - 검색어: 모든 속성 문자열화 후 부분일치.
 * 해당 객체 타입에 없는 속성 필터는 무시한다.
 */
export function applyRuntimeFilters(
  objects: readonly GenericObject[],
  state: RuntimeState,
): GenericObject[] {
  const activeFilters = Object.entries(state.filters).filter(([, filter]) =>
    filter.kind === "values"
      ? filter.values.length > 0
      : filter.min !== null || filter.max !== null,
  );
  const search = state.searchText.trim().toLowerCase();
  return objects.filter((object) => {
    for (const [property, filter] of activeFilters) {
      if (!(property in object.properties)) continue;
      const raw = object.properties[property];
      if (filter.kind === "values") {
        if (!filter.values.includes(String(raw ?? ""))) return false;
      } else {
        if (typeof raw !== "number") return false;
        if (filter.min !== null && raw < filter.min) return false;
        if (filter.max !== null && raw > filter.max) return false;
      }
    }
    if (search.length > 0) {
      const haystack = Object.values(object.properties)
        .map((value) => String(value ?? ""))
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
}

/** 패싯 값별 카운트 (필터 위젯 히스토그램). */
export function facetCounts(
  objects: readonly GenericObject[],
  property: string,
): { value: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const object of objects) {
    const value = String(object.properties[property] ?? "—");
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count);
}

/** 숫자 속성의 최소·최대 (범위 필터 슬라이더 경계). */
export function numericExtent(
  objects: readonly GenericObject[],
  property: string,
): { min: number; max: number } | null {
  let min = Infinity;
  let max = -Infinity;
  for (const object of objects) {
    const value = object.properties[property];
    if (typeof value === "number" && Number.isFinite(value)) {
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  return min === Infinity ? null : { min, max };
}

export function useFilteredObjects(
  objects: readonly GenericObject[],
  state: RuntimeState,
): GenericObject[] {
  return useMemo(() => applyRuntimeFilters(objects, state), [objects, state]);
}
