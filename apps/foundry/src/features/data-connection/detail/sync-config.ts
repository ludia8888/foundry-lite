import type { SourceManagedSync } from "@foundry-lite/sdk";

/** managed sync의 schedule(Record)을 화면 모델로 파싱한다. */
export interface SyncScheduleView {
  mode: "manual" | "disabled" | "interval" | "cron";
  everySeconds: number | null;
  cron: string | null;
  batchLimit: number | null;
  autoPauseAfterFailures: number;
}

export function readSchedule(sync: SourceManagedSync): SyncScheduleView {
  const schedule = sync.schedule ?? {};
  const mode =
    schedule.mode === "interval" ||
    schedule.mode === "cron" ||
    schedule.mode === "disabled"
      ? schedule.mode
      : "manual";
  const everySecondsRaw = schedule.everySeconds ?? schedule.intervalSeconds;
  return {
    mode,
    everySeconds:
      typeof everySecondsRaw === "number" && everySecondsRaw > 0
        ? everySecondsRaw
        : null,
    cron: typeof schedule.cron === "string" ? schedule.cron : null,
    batchLimit:
      typeof schedule.batchLimit === "number" ? schedule.batchLimit : null,
    autoPauseAfterFailures:
      typeof schedule.autoPauseAfterFailures === "number"
        ? schedule.autoPauseAfterFailures
        : 3,
  };
}

export function scheduleSummary(view: SyncScheduleView): string {
  if (view.mode === "disabled") return "일시 중지됨";
  if (view.mode === "interval" && view.everySeconds) {
    return `${view.everySeconds}초 간격`;
  }
  if (view.mode === "cron" && view.cron) {
    return `cron ${view.cron}`;
  }
  return "수동 (빌드 버튼으로 실행)";
}

/**
 * configSummary에서 SQL 쿼리를 찾거나, postgres 계약(tableName+checkpointColumn)
 * 으로부터 실행 쿼리를 파생한다.
 */
export function readSyncQuery(sync: SourceManagedSync): string | null {
  const config = sync.configSummary ?? {};
  const query = config.query ?? config.sql;
  if (typeof query === "string" && query.trim().length > 0) return query;
  if (typeof config.tableName === "string" && config.tableName.length > 0) {
    const checkpoint =
      typeof config.checkpointColumn === "string" && config.checkpointColumn
        ? `\nWHERE ${config.checkpointColumn} > :lastValue`
        : "";
    return `SELECT * FROM ${config.tableName}${checkpoint}`;
  }
  return null;
}

export function readSyncPreQuery(sync: SourceManagedSync): string | null {
  const preQuery = sync.configSummary?.preQuery;
  return typeof preQuery === "string" && preQuery.trim().length > 0
    ? preQuery
    : null;
}

export interface SyncIncrementalView {
  column: string;
  initialValue: string;
}

/** checkpointColumn(백엔드 계약) 또는 incremental 객체 — 점진적 동기화 설정. */
export function readSyncIncremental(
  sync: SourceManagedSync,
): SyncIncrementalView | null {
  const config = sync.configSummary ?? {};
  if (
    typeof config.checkpointColumn === "string" &&
    config.checkpointColumn.length > 0
  ) {
    const lastValue = sync.checkpoint?.lastValue;
    return {
      column: config.checkpointColumn,
      initialValue:
        typeof lastValue === "string" || typeof lastValue === "number"
          ? String(lastValue)
          : "",
    };
  }
  const incremental = config.incremental;
  if (typeof incremental !== "object" || incremental === null) return null;
  const record = incremental as Record<string, unknown>;
  if (typeof record.column !== "string" || record.column.length === 0) {
    return null;
  }
  return {
    column: record.column,
    initialValue:
      typeof record.initialValue === "string" ||
      typeof record.initialValue === "number"
        ? String(record.initialValue)
        : "",
  };
}

/** SQL/incremental 등 알려진 키를 제외한 나머지 소스별 구성. */
export function readRemainingConfig(
  sync: SourceManagedSync,
): Record<string, unknown> {
  const config = { ...(sync.configSummary ?? {}) };
  delete config.query;
  delete config.sql;
  delete config.preQuery;
  delete config.incremental;
  return config;
}

export const TRANSACTION_MODES = [
  {
    value: "SNAPSHOT",
    label: "SNAPSHOT",
    description: "가져온 데이터가 이전 데이터를 덮어씁니다",
  },
  {
    value: "APPEND",
    label: "APPEND",
    description: "가져온 데이터가 증분적으로 추가됩니다",
  },
] as const;
