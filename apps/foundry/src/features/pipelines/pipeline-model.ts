import type {
  PipelineEdge,
  PipelineGraph,
  PipelineNode,
  PipelineNodeType,
  PipelineSchemaColumn,
} from "@foundry-lite/sdk";

export type NodePosition = { x: number; y: number };
export type PositionsByNodeId = Record<string, NodePosition>;

export type PipelineValidationIssue = Record<string, unknown> & {
  code: string;
};

export type PipelineValidationResult = {
  valid: boolean;
  errors: PipelineValidationIssue[];
  warnings: PipelineValidationIssue[];
  fingerprint: string;
};

export type NodeTypeMeta = {
  label: string;
  shortLabel: string;
  headerClassName: string;
  /** 헤더 좌측 아이콘 셀 배경 (공식 스크린샷: 연회색/연보라 등 연톤). */
  iconCellClassName: string;
  /** 헤더 좌측 아이콘 셀 안 아이콘 색 (공식 스크린샷: 아이콘은 고유색 유지). */
  iconClassName: string;
  isSolidHeader: boolean;
};

/**
 * Palantir Pipeline Builder 노드 색상 언어 (samplegraph-2x 실측):
 * 소스=연회색 타이틀 행, 변환=뮤트 그린 #83C485, 조인/유니온=민트 #7BC3BB(아이콘 셀 연보라),
 * 선택/캐스트=앰버 #DCBC79, 출력=솔리드 블루 #147DB3.
 */
export const NODE_TYPE_META: Record<PipelineNodeType, NodeTypeMeta> = {
  dataset: {
    label: "데이터셋 입력",
    shortLabel: "데이터셋",
    headerClassName: "bg-[#F5F6F7] text-foreground",
    iconCellClassName: "bg-[#F5F6F7]",
    iconClassName: "text-[#4C90F0]",
    isSolidHeader: false,
  },
  sql: {
    label: "SQL 변환",
    shortLabel: "SQL",
    headerClassName: "bg-[#83C485] text-white",
    iconCellClassName: "bg-[#EDF1F7]",
    iconClassName: "text-[#4C90F0]",
    isSolidHeader: true,
  },
  python: {
    label: "Python 변환",
    shortLabel: "Python",
    headerClassName: "bg-[#83C485] text-white",
    iconCellClassName: "bg-[#EDF1F7]",
    iconClassName: "text-[#4C90F0]",
    isSolidHeader: true,
  },
  join: {
    label: "조인",
    shortLabel: "조인",
    headerClassName: "bg-[#7BC3BB] text-white",
    iconCellClassName: "bg-[#EFEBFA]",
    iconClassName: "text-[#A48BE8]",
    isSolidHeader: true,
  },
  union: {
    label: "유니온",
    shortLabel: "유니온",
    headerClassName: "bg-[#7BC3BB] text-white",
    iconCellClassName: "bg-[#EFEBFA]",
    iconClassName: "text-[#A48BE8]",
    isSolidHeader: true,
  },
  select_cast: {
    label: "컬럼 선택/캐스트",
    shortLabel: "선택/캐스트",
    headerClassName: "bg-[#DCBC79] text-white",
    iconCellClassName: "bg-[#EDF1F7]",
    iconClassName: "text-[#4C90F0]",
    isSolidHeader: true,
  },
  output_dataset: {
    label: "데이터셋 출력",
    shortLabel: "출력",
    headerClassName: "bg-[#147DB3] text-white",
    iconCellClassName: "bg-white",
    iconClassName: "text-[#147DB3]",
    isSolidHeader: true,
  },
};

export function emptyGraphDoc(): PipelineGraph {
  return {
    nodes: [],
    edges: [],
    layout: {},
    outputContract: { columns: [] },
    tests: [],
    schedule: null,
  };
}

export function normalizeGraphDoc(
  graph: PipelineGraph | null | undefined,
): PipelineGraph {
  if (!graph) return emptyGraphDoc();
  return {
    ...emptyGraphDoc(),
    ...graph,
    nodes: graph.nodes ?? [],
    edges: graph.edges ?? [],
  };
}

export function nodeLabel(node: PipelineNode): string {
  const data = nodeDataOf(node);
  const label = data.label;
  if (typeof label === "string" && label.length > 0) return label;
  const ref = data.datasetRef ?? data.outputDatasetRef;
  if (typeof ref === "string" && ref.length > 0)
    return ref.split(/[/.]/).pop() ?? ref;
  return node.id;
}

/** 도트(신규 표준)와 슬래시(레거시 그래프) ref를 모두 [namespace, name]으로 분해한다. */
export function splitDatasetRef(ref: string | null): [string, string] {
  if (!ref) return ["", ""];
  const separator = ref.includes(".") ? "." : "/";
  const index = ref.indexOf(separator);
  if (index < 0) return [ref, ""];
  return [ref.slice(0, index), ref.slice(index + 1)];
}

export function nodeDataOf(node: PipelineNode): Record<string, unknown> {
  return { ...(node.config ?? {}), ...(node.data ?? {}) };
}

export function nodeDatasetRef(node: PipelineNode): string | null {
  const data = nodeDataOf(node);
  const ref = data.datasetRef ?? data.outputDatasetRef;
  return typeof ref === "string" && ref.length > 0 ? ref : null;
}

export function parseValidation(
  payload: Record<string, unknown> | null | undefined,
): PipelineValidationResult | null {
  if (!payload || typeof payload.valid !== "boolean") return null;
  return {
    valid: payload.valid,
    errors: issueList(payload.errors),
    warnings: issueList(payload.warnings),
    fingerprint:
      typeof payload.fingerprint === "string" ? payload.fingerprint : "",
  };
}

function issueList(value: unknown): PipelineValidationIssue[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is PipelineValidationIssue =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as { code?: unknown }).code === "string",
  );
}

export function issuesByNodeId(
  issues: readonly PipelineValidationIssue[],
): Record<string, PipelineValidationIssue[]> {
  const grouped: Record<string, PipelineValidationIssue[]> = {};
  for (const issue of issues) {
    const nodeId = issue.nodeId;
    if (typeof nodeId !== "string") continue;
    grouped[nodeId] = [...(grouped[nodeId] ?? []), issue];
  }
  return grouped;
}

export function graphPositions(graph: PipelineGraph): PositionsByNodeId {
  const layout = graph.layout;
  const raw =
    layout && typeof layout === "object"
      ? (layout as Record<string, unknown>).positions
      : null;
  if (!raw || typeof raw !== "object") return {};
  const positions: PositionsByNodeId = {};
  for (const [nodeId, value] of Object.entries(
    raw as Record<string, unknown>,
  )) {
    if (
      typeof value === "object" &&
      value !== null &&
      typeof (value as { x?: unknown }).x === "number" &&
      typeof (value as { y?: unknown }).y === "number"
    ) {
      positions[nodeId] = {
        x: (value as { x: number }).x,
        y: (value as { y: number }).y,
      };
    }
  }
  return positions;
}

export function withLayoutPositions(
  graph: PipelineGraph,
  positions: PositionsByNodeId,
): PipelineGraph {
  return { ...graph, layout: { ...(graph.layout ?? {}), positions } };
}

const COLUMN_GAP = 280;
const ROW_GAP = 130;

/** 토폴로지 컬럼 기반 자동 배치: 소스 → 변환 → 출력이 좌→우로 흐른다. */
export function autoLayoutPositions(graph: PipelineGraph): PositionsByNodeId {
  const depthByNodeId = nodeDepths(graph);
  const rowsUsedByDepth: Record<number, number> = {};
  const positions: PositionsByNodeId = {};
  for (const node of graph.nodes) {
    const depth = depthByNodeId[node.id] ?? 0;
    const row = rowsUsedByDepth[depth] ?? 0;
    rowsUsedByDepth[depth] = row + 1;
    positions[node.id] = {
      x: 40 + depth * COLUMN_GAP,
      y: 40 + row * ROW_GAP + (depth % 2) * 32,
    };
  }
  return positions;
}

function nodeDepths(graph: PipelineGraph): Record<string, number> {
  const depths: Record<string, number> = {};
  for (const nodeId of topologicalNodeIds(graph)) {
    const incoming = graph.edges.filter((edge) => edge.target === nodeId);
    const depth = incoming.reduce(
      (max, edge) => Math.max(max, (depths[edge.source] ?? 0) + 1),
      0,
    );
    depths[nodeId] = depth;
  }
  return depths;
}

export function topologicalNodeIds(graph: PipelineGraph): string[] {
  const ids = graph.nodes.map((node) => node.id);
  const incomingCount: Record<string, number> = Object.fromEntries(
    ids.map((id) => [id, 0]),
  );
  const outgoing: Record<string, string[]> = Object.fromEntries(
    ids.map((id) => [id, []]),
  );
  for (const edge of graph.edges) {
    if (!(edge.source in outgoing) || !(edge.target in incomingCount)) continue;
    incomingCount[edge.target] += 1;
    outgoing[edge.source] = [...outgoing[edge.source], edge.target];
  }
  const ready = ids.filter((id) => incomingCount[id] === 0).sort();
  const ordered: string[] = [];
  const queue = [...ready];
  while (queue.length > 0) {
    const current = queue.shift();
    if (current === undefined) break;
    ordered.push(current);
    for (const target of outgoing[current]) {
      incomingCount[target] -= 1;
      if (incomingCount[target] === 0) queue.push(target);
    }
  }
  return ordered.length === ids.length ? ordered : ids;
}

/**
 * 클라이언트 측 스키마 전파: 백엔드 previewNode가 node.schema를 근거로 응답하므로
 * 저장 전에 각 노드의 파생 스키마를 채워 둔다.
 */
export function deriveNodeSchemas(graph: PipelineGraph): PipelineGraph {
  const schemaByNodeId: Record<string, PipelineSchemaColumn[]> = {};
  for (const node of graph.nodes) {
    if (node.type === "dataset" && node.schema)
      schemaByNodeId[node.id] = node.schema;
  }
  for (const nodeId of topologicalNodeIds(graph)) {
    const node = graph.nodes.find((candidate) => candidate.id === nodeId);
    if (!node || node.type === "dataset") continue;
    const inputSchemas = graph.edges
      .filter((edge) => edge.target === nodeId)
      .map((edge) => schemaByNodeId[edge.source] ?? []);
    schemaByNodeId[nodeId] = derivedSchemaFor(node, inputSchemas);
  }
  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({
      ...node,
      schema: schemaByNodeId[node.id] ?? node.schema ?? [],
    })),
  };
}

function derivedSchemaFor(
  node: PipelineNode,
  inputSchemas: readonly PipelineSchemaColumn[][],
): PipelineSchemaColumn[] {
  if (node.type === "join") return mergeColumns(inputSchemas);
  if (node.type === "sql" || node.type === "python") return node.schema ?? [];
  return inputSchemas[0] ?? [];
}

function mergeColumns(
  inputSchemas: readonly PipelineSchemaColumn[][],
): PipelineSchemaColumn[] {
  const merged: PipelineSchemaColumn[] = [];
  const seen = new Set<string>();
  for (const schema of inputSchemas) {
    for (const column of schema) {
      if (seen.has(column.name)) continue;
      seen.add(column.name);
      merged.push(column);
    }
  }
  return merged;
}

export function nextNodeId(
  type: PipelineNodeType,
  existingIds: readonly string[],
): string {
  const taken = new Set(existingIds);
  for (let index = 1; index <= 999; index += 1) {
    const candidate = `${type}_${index}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${type}_${Date.now()}`;
}

export function createDatasetNode(
  id: string,
  datasetRef: string,
  schema: PipelineSchemaColumn[],
): PipelineNode {
  const label = datasetRef.split(/[/.]/).pop() ?? datasetRef;
  return { id, type: "dataset", data: { datasetRef, label }, schema };
}

export function createTransformNode(
  id: string,
  type: PipelineNodeType,
  pipelineId: string,
): PipelineNode {
  const slug = pipelineId.replace(/[^a-zA-Z0-9_]/g, "_");
  const base: Record<string, unknown> = {
    label: NODE_TYPE_META[type].label,
    outputDatasetRef: `pipelines.${slug}_${id}`,
  };
  if (type === "join") {
    return {
      id,
      type,
      data: { ...base, leftKey: "", rightKey: "", joinType: "inner" },
      schema: [],
    };
  }
  if (type === "sql") {
    return {
      id,
      type,
      data: { ...base, sql: DEFAULT_SQL_TEMPLATE },
      schema: [],
    };
  }
  return { id, type, data: base, schema: [] };
}

export function createGraphEdge(
  source: string,
  target: string,
  existing: readonly PipelineEdge[],
): PipelineEdge {
  const id = `edge_${source}__${target}_${existing.length + 1}`;
  return { id, source, target };
}

export const SQL_INPUT_PLACEHOLDER = "{{ inputs[0] }}";
export const DEFAULT_SQL_TEMPLATE = `SELECT * FROM ${SQL_INPUT_PLACEHOLDER}`;

/**
 * sql 노드에 입력을 연결할 때 기본 템플릿 placeholder를 백엔드 실행 형식
 * {{ input('<dataset_ref>') }}로 치환한다 (런타임은 이 형식만 substitute).
 * 손대지 않은 기본 템플릿이면 upstream 스키마 기반 명시적 컬럼 목록으로
 * 스캐폴딩한다 (SELECT * 는 스토리지 파티션 컬럼까지 끌고 온다).
 */
export function withSqlInputTemplate(
  graph: PipelineGraph,
  sourceId: string,
  targetId: string,
): PipelineGraph {
  const target = graph.nodes.find((node) => node.id === targetId);
  if (!target || target.type !== "sql") return graph;
  const sql = nodeDataOf(target).sql;
  if (typeof sql !== "string" || !sql.includes(SQL_INPUT_PLACEHOLDER))
    return graph;
  const source = graph.nodes.find((node) => node.id === sourceId);
  const sourceRef = source ? nodeDatasetRef(source) : null;
  if (!sourceRef) return graph;
  const inputMacro = `{{ input('${sourceRef}') }}`;
  const columnNames = (source?.schema ?? []).map((column) => column.name);
  const nextSql =
    sql === DEFAULT_SQL_TEMPLATE && columnNames.length > 0
      ? `SELECT ${columnNames.join(", ")} FROM ${inputMacro}`
      : sql.replace(SQL_INPUT_PLACEHOLDER, inputMacro);
  return {
    ...graph,
    nodes: graph.nodes.map((node) =>
      node.id === targetId
        ? { ...node, data: { ...(node.data ?? {}), sql: nextSql } }
        : node,
    ),
  };
}

export function shortFingerprint(
  fingerprint: string | null | undefined,
): string {
  return fingerprint ? fingerprint.slice(0, 10) : "-";
}

export function asGraph(value: unknown): PipelineGraph | null {
  if (typeof value !== "object" || value === null) return null;
  const graph = value as PipelineGraph;
  return Array.isArray(graph.nodes) && Array.isArray(graph.edges)
    ? graph
    : null;
}

export type GraphNodeChangeKind = "added" | "removed" | "modified";

export type GraphNodeChange = {
  kind: GraphNodeChangeKind;
  nodeId: string;
  nodeType: string;
  label: string;
  schemaBefore: PipelineSchemaColumn[];
  schemaAfter: PipelineSchemaColumn[];
};

/** base 그래프와 제안 그래프를 노드 id 기준으로 비교해 추가/삭제/변경 목록을 만든다. */
export function diffGraphNodes(
  base: PipelineGraph,
  next: PipelineGraph,
): GraphNodeChange[] {
  const baseById = new Map(base.nodes.map((node) => [node.id, node]));
  const nextIds = new Set(next.nodes.map((node) => node.id));
  const changes: GraphNodeChange[] = [];
  for (const node of next.nodes) {
    const before = baseById.get(node.id);
    if (!before) {
      changes.push(nodeChange("added", null, node));
    } else if (stableStringify(before) !== stableStringify(node)) {
      changes.push(nodeChange("modified", before, node));
    }
  }
  for (const node of base.nodes) {
    if (!nextIds.has(node.id)) changes.push(nodeChange("removed", node, null));
  }
  return changes;
}

function nodeChange(
  kind: GraphNodeChangeKind,
  before: PipelineNode | null,
  after: PipelineNode | null,
): GraphNodeChange {
  const node = after ?? before;
  return {
    kind,
    nodeId: node?.id ?? "",
    nodeType: node?.type ?? "",
    label: node ? nodeLabel(node) : "",
    schemaBefore: before?.schema ?? [],
    schemaAfter: after?.schema ?? [],
  };
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export type SchemaColumnDiffRow = {
  name: string;
  before: string | null;
  after: string | null;
};

/** 스키마 Before/After를 컬럼 이름 기준으로 정렬해 나란히 비교한다. */
export function diffSchemaColumns(
  before: readonly PipelineSchemaColumn[],
  after: readonly PipelineSchemaColumn[],
): SchemaColumnDiffRow[] {
  const beforeByName = new Map(
    before.map((column) => [column.name, column.type]),
  );
  const afterByName = new Map(
    after.map((column) => [column.name, column.type]),
  );
  const names = [
    ...before.map((column) => column.name),
    ...after
      .filter((column) => !beforeByName.has(column.name))
      .map((column) => column.name),
  ];
  return names.map((name) => ({
    name,
    before: beforeByName.get(name) ?? null,
    after: afterByName.get(name) ?? null,
  }));
}

export type BranchDiffSection = {
  changed: boolean;
  baseFingerprint: string;
  graphFingerprint: string;
};

export type BranchDiffSummary = {
  baseVersionId: string | null;
  latestVersionId: string | null;
  baseStale: boolean;
  graph: BranchDiffSection | null;
};

export function parseBranchDiff(
  payload: Record<string, unknown> | null | undefined,
): BranchDiffSummary | null {
  if (!payload) return null;
  return {
    baseVersionId: asText(payload.baseVersionId),
    latestVersionId: asText(payload.latestVersionId),
    baseStale: payload.baseStale === true,
    graph: diffSection(payload.graph),
  };
}

function diffSection(value: unknown): BranchDiffSection | null {
  if (typeof value !== "object" || value === null) return null;
  const section = value as Record<string, unknown>;
  if (typeof section.changed !== "boolean") return null;
  return {
    changed: section.changed,
    baseFingerprint: asText(section.baseFingerprint) ?? "",
    graphFingerprint: asText(section.graphFingerprint) ?? "",
  };
}

/** 스케줄 정의(JSON)를 사람이 읽는 한 줄 요약으로 바꾼다. */
export function scheduleLabel(value: unknown): string {
  if (typeof value !== "object" || value === null) return "-";
  const schedule = value as Record<string, unknown>;
  const cron = asText(schedule.cron);
  if (cron) return `cron ${cron}`;
  const minutes = schedule.intervalMinutes;
  if (typeof minutes === "number" && Number.isFinite(minutes))
    return `매 ${minutes}분`;
  return JSON.stringify(schedule);
}

export function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", { hour12: false });
}

export function asText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
