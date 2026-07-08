import type { Dataset, TransformSchedulerDecision } from "@foundry-lite/sdk";

/** IDE 좌측 트리에 노출할 등록된 SQL transform 항목. */
export type RegisteredTransform = {
  apiName: string;
  transformId: string;
  outputDatasetRef: string;
  mode: string;
  reason: string;
  due: boolean;
  activeRunId: string | null;
  lastSuccessfulRunId: string | null;
};

export function toRegisteredTransform(
  decision: TransformSchedulerDecision,
): RegisteredTransform {
  return {
    apiName: decision.apiName,
    transformId: decision.transformId,
    outputDatasetRef: decision.outputDatasetRef,
    mode: decision.mode,
    reason: decision.reason,
    due: decision.due,
    activeRunId: decision.activeRunId,
    lastSuccessfulRunId: decision.lastSuccessfulRunId,
  };
}

/** dataset ref (`namespace.name`)로 /datasets 프리뷰 딥링크 경로를 만든다. */
export function datasetPreviewHref(datasetRef: string): string {
  return `/datasets?ref=${encodeURIComponent(datasetRef)}`;
}

export function datasetRef(dataset: Dataset): string {
  return `${dataset.namespace}.${dataset.name}`;
}

/** 트리에서 namespace별로 데이터셋을 묶는다. */
export function groupDatasetsByNamespace(
  datasets: readonly Dataset[],
): Array<{ namespace: string; datasets: Dataset[] }> {
  const byNamespace = new Map<string, Dataset[]>();
  for (const dataset of datasets) {
    const bucket = byNamespace.get(dataset.namespace) ?? [];
    bucket.push(dataset);
    byNamespace.set(dataset.namespace, bucket);
  }
  return [...byNamespace.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([namespace, items]) => ({
      namespace,
      datasets: items.sort((a, b) => a.name.localeCompare(b.name)),
    }));
}

/** transform SQL 안에서 선언 입력을 참조하는 플랫폼 placeholder 문법. */
export function inputPlaceholder(ref: string): string {
  return `{{ input('${ref}') }}`;
}

/** 선언된 inputs 맵에서 SQL이 참조하지 않는 미사용 ref를 찾아 경고에 사용한다. */
export function unreferencedInputs(
  sql: string,
  inputs: Readonly<Record<string, string>>,
): string[] {
  return Object.values(inputs).filter(
    (ref) => !sql.includes(inputPlaceholder(ref)),
  );
}

/** SQL이 실제 참조하는 입력만 빌드 payload로 전달한다. */
export function referencedInputs(
  sql: string,
  inputs: Readonly<Record<string, string>>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(inputs).filter(([, ref]) =>
      sql.includes(inputPlaceholder(ref)),
    ),
  );
}

const SQL_KEYWORDS = new Set([
  "select",
  "from",
  "where",
  "group",
  "by",
  "order",
  "having",
  "limit",
  "join",
  "left",
  "right",
  "inner",
  "outer",
  "full",
  "on",
  "as",
  "and",
  "or",
  "not",
  "in",
  "is",
  "null",
  "distinct",
  "count",
  "sum",
  "avg",
  "min",
  "max",
  "case",
  "when",
  "then",
  "else",
  "end",
  "union",
  "all",
  "with",
  "cast",
  "coalesce",
  "over",
  "partition",
  "asc",
  "desc",
  "between",
  "like",
  "exists",
  "offset",
]);

type Token = {
  text: string;
  kind: "keyword" | "string" | "comment" | "input" | "number" | "plain";
};

/**
 * mono 에디터용 경량 SQL 토크나이저.
 * (전체 파서가 아니라 키워드/문자열/주석/입력 placeholder를 색으로 구분하는 하이라이터)
 */
export function tokenizeSql(sql: string): Token[] {
  const tokens: Token[] = [];
  const pattern =
    /(\{\{\s*input\('[^']*'\)\s*\}\})|(--[^\n]*)|('(?:[^'\\]|\\.)*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([^\s])/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(sql)) !== null) {
    const [text, input, comment, str, num, word] = match;
    if (input) tokens.push({ text, kind: "input" });
    else if (comment) tokens.push({ text, kind: "comment" });
    else if (str) tokens.push({ text, kind: "string" });
    else if (num) tokens.push({ text, kind: "number" });
    else if (word && SQL_KEYWORDS.has(word.toLowerCase()))
      tokens.push({ text, kind: "keyword" });
    else tokens.push({ text, kind: "plain" });
  }
  return tokens;
}

/** acceptance user flow 시작용 기본 SQL: clean.orders 대상 SELECT. */
export const SAMPLE_SQL = `SELECT
  order_id,
  amount,
  region
FROM {{ input('clean.orders') }}
WHERE amount > 500`;

/** 실패 케이스 재현용 잘못된 SQL (존재하지 않는 컬럼). */
export const FAILING_SQL = `SELECT nonexistent_column
FROM {{ input('clean.orders') }}`;

/**
 * partial 화면에서 미구현으로 남겨둔 IDE 고급 기능.
 * fabricate하지 않고 future 배지로 정직하게 노출한다.
 */
export const FUTURE_FEATURES = [
  "Git / Branch 관리",
  "Pull request · 코드 리뷰",
  "Preview diff",
  "Debugger · breakpoint",
  "Python transform 런타임",
  "패키지 / 의존성 관리",
] as const;
