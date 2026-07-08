import type {
  Dataset,
  DatasetVersion,
  LineageEdge,
  RuntimeRow,
} from "@foundry-lite/sdk";
import type { FoundryLiteOperationsRunRow } from "@foundry-lite/sdk/react";

import { runTypeFromRunId } from "@/lib/operations-links";

/** 리니지 그래프 노드 종류 (이미지 근거: dataset / data-source / object-type 노드). */
export type LineageNodeKind = "dataset" | "data_source" | "object_type";

export type LineageBuildStatus = "success" | "failed" | "running" | "unknown";

export type LineageResourceKind =
  "source_sync" | "transform_output" | "data_source" | "object_type";

export type LineageColorMode = "resource" | "build" | "stale";

export interface LineageGraphNode {
  id: string;
  kind: LineageNodeKind;
  label: string;
  detail: string | null;
  datasetRef: string | null;
  namespace: string | null;
  name: string | null;
  datasetId: string | null;
  latestVersion: DatasetVersion | null;
  versionIds: readonly string[];
  isStale: boolean;
  buildStatus: LineageBuildStatus;
  resourceKind: LineageResourceKind;
}

export interface LineageGraphEdge {
  id: string;
  fromNodeId: string;
  toNodeId: string;
  relation: string;
  createdByRunId: string | null;
  fromVersionId: string | null;
  toVersionId: string | null;
}

export interface LineageGraphModel {
  nodes: LineageGraphNode[];
  edges: LineageGraphEdge[];
  nodesById: ReadonlyMap<string, LineageGraphNode>;
}

export interface LineageGraphSource {
  datasets: Dataset[];
  versionsByDatasetRef: Record<string, DatasetVersion[]>;
  lineageEdges: LineageEdge[];
}

export interface NodeColorway {
  background: string;
  border: string;
  text: string;
}

/** Palantir Data Lineage 스크린샷 픽셀 실측 기반 플랫 컬러웨이. */
export const NODE_COLORWAYS = {
  syncGreen: { background: "#47A64B", border: "#3C8A3F", text: "#FFFFFF" },
  transformTan: { background: "#F2B071", border: "#DB9757", text: "#4A2E12" },
  uploadedBlue: { background: "#8AB9E8", border: "#5E97CF", text: "#173F66" },
  // build-helper.png 실측: out-of-date 노드 fill (221,187,154) / 텍스트 (140,148,155)
  staleTan: { background: "#DDBB9A", border: "#C9A275", text: "#6B7580" },
  // build-all-ancestors.png 실측: up-to-date 노드 fill (159,186,234) / 흰 텍스트
  upToDateBlue: { background: "#9FBAEA", border: "#7E9FDB", text: "#FFFFFF" },
  failedRed: { background: "#DB3737", border: "#B22D2D", text: "#FFFFFF" },
  runningBlue: { background: "#2D72D2", border: "#215DB0", text: "#FFFFFF" },
  // 실측: 버전 없는 흰 노드 (245,248,250) / 회색 텍스트 (140,148,155)
  neutralGray: { background: "#F5F8FA", border: "#C5CBD3", text: "#6B7580" },
  sourceSlate: { background: "#5F6B7C", border: "#47525F", text: "#FFFFFF" },
  objectTeal: { background: "#7ED6CE", border: "#4FB5AB", text: "#0F4B45" },
} as const satisfies Record<string, NodeColorway>;

export const SELECTION_ORANGE = "#E8853D";
/** 실측 (219,147,76): 빌드 경로 하이라이트 엣지 오렌지. */
export const EDGE_ORANGE = "#DB934C";
/** 실측 (141,155,167): 기본 엣지 회색. */
export const EDGE_GRAY = "#8D9BA7";
/** 실측 (147,86,16): "1 node selected" 오렌지 텍스트. */
export const SELECTED_TEXT_ORANGE = "#935610";

const RANK_X_GAP = 250;
const ROW_Y_GAP = 96;
const LAYOUT_X0 = 60;
const LAYOUT_Y0 = 60;

function readString(row: RuntimeRow, key: string): string | null {
  const value = row[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readRecord(
  row: RuntimeRow,
  key: string,
): Record<string, unknown> | null {
  const value = row[key];
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

export function datasetRefOf(dataset: Dataset): string {
  return `${dataset.namespace}.${dataset.name}`;
}

export function datasetNodeId(ref: string): string {
  return `dataset:${ref}`;
}

export { runTypeFromRunId };

interface DatasetIndexes {
  datasetByVersionId: Map<string, Dataset>;
  datasetById: Map<string, Dataset>;
  versionIdsByRef: Map<string, string[]>;
  latestVersionByRef: Map<string, DatasetVersion>;
}

function buildDatasetIndexes(source: LineageGraphSource): DatasetIndexes {
  const datasetByVersionId = new Map<string, Dataset>();
  const datasetById = new Map<string, Dataset>();
  const versionIdsByRef = new Map<string, string[]>();
  const latestVersionByRef = new Map<string, DatasetVersion>();

  for (const dataset of source.datasets) {
    const ref = datasetRefOf(dataset);
    const versions = source.versionsByDatasetRef[ref] ?? [];
    datasetById.set(dataset.id, dataset);
    versionIdsByRef.set(
      ref,
      versions.map((version) => version.id),
    );
    if (versions[0]) latestVersionByRef.set(ref, versions[0]);
    for (const version of versions) datasetByVersionId.set(version.id, dataset);
  }
  return {
    datasetByVersionId,
    datasetById,
    versionIdsByRef,
    latestVersionByRef,
  };
}

function createDatasetNode(
  dataset: Dataset,
  indexes: DatasetIndexes,
): LineageGraphNode {
  const ref = datasetRefOf(dataset);
  return {
    id: datasetNodeId(ref),
    kind: "dataset",
    label: ref,
    detail: dataset.storage_kind,
    datasetRef: ref,
    namespace: dataset.namespace,
    name: dataset.name,
    datasetId: dataset.id,
    latestVersion: indexes.latestVersionByRef.get(ref) ?? null,
    versionIds: indexes.versionIdsByRef.get(ref) ?? [],
    isStale: false,
    buildStatus: "unknown",
    resourceKind: "source_sync",
  };
}

function applyLineageEdges(
  source: LineageGraphSource,
  indexes: DatasetIndexes,
  nodes: Map<string, LineageGraphNode>,
): LineageGraphEdge[] {
  const edges: LineageGraphEdge[] = [];
  const seenEdgeIds = new Set<string>();

  for (const edge of source.lineageEdges) {
    if (seenEdgeIds.has(edge.id)) continue;
    seenEdgeIds.add(edge.id);
    const fromDataset = indexes.datasetByVersionId.get(edge.from_resource_id);
    const toDataset = indexes.datasetByVersionId.get(edge.to_resource_id);
    if (!fromDataset || !toDataset) continue;

    const fromRef = datasetRefOf(fromDataset);
    const toRef = datasetRefOf(toDataset);
    edges.push({
      id: edge.id,
      fromNodeId: datasetNodeId(fromRef),
      toNodeId: datasetNodeId(toRef),
      relation: edge.relation,
      createdByRunId: edge.created_by_run_id,
      fromVersionId: edge.from_resource_id,
      toVersionId: edge.to_resource_id,
    });

    const toNode = nodes.get(datasetNodeId(toRef));
    if (toNode) {
      const latestUpstream = indexes.latestVersionByRef.get(fromRef);
      const isStale = Boolean(
        latestUpstream && latestUpstream.id !== edge.from_resource_id,
      );
      nodes.set(datasetNodeId(toRef), {
        ...toNode,
        resourceKind: "transform_output",
        isStale: toNode.isStale || isStale,
      });
    }
  }
  return edges;
}

function runBuildStatus(run: FoundryLiteOperationsRunRow): LineageBuildStatus {
  if (run.isFailure) return "failed";
  if (run.isRunning || run.isPending) return "running";
  if (run.isSucceeded) return "success";
  return "unknown";
}

function applySyncRun(
  run: FoundryLiteOperationsRunRow,
  indexes: DatasetIndexes,
  nodes: Map<string, LineageGraphNode>,
  edges: LineageGraphEdge[],
): void {
  const outputDatasetId = readString(run.row, "output_dataset_id");
  const syncName = readString(run.row, "sync_name") ?? run.runId ?? "sync";
  const dataset = outputDatasetId
    ? indexes.datasetById.get(outputDatasetId)
    : undefined;
  if (!dataset) return;

  const sourceNodeId = `source:${syncName}`;
  if (!nodes.has(sourceNodeId)) {
    nodes.set(sourceNodeId, {
      id: sourceNodeId,
      kind: "data_source",
      label: syncName,
      detail: readString(run.row, "source_type"),
      datasetRef: null,
      namespace: null,
      name: syncName,
      datasetId: null,
      latestVersion: null,
      versionIds: [],
      isStale: false,
      buildStatus: runBuildStatus(run),
      resourceKind: "data_source",
    });
  }
  const targetNodeId = datasetNodeId(datasetRefOf(dataset));
  edges.push({
    id: `sync-${run.runId ?? syncName}`,
    fromNodeId: sourceNodeId,
    toNodeId: targetNodeId,
    relation: "sync",
    createdByRunId: run.runId,
    fromVersionId: null,
    toVersionId: readString(run.row, "committed_version_id"),
  });
  const targetNode = nodes.get(targetNodeId);
  if (targetNode && targetNode.buildStatus === "unknown") {
    nodes.set(targetNodeId, {
      ...targetNode,
      buildStatus: runBuildStatus(run),
    });
  }
}

function applyIndexRun(
  run: FoundryLiteOperationsRunRow,
  indexes: DatasetIndexes,
  nodes: Map<string, LineageGraphNode>,
  edges: LineageGraphEdge[],
): void {
  const apiName = readString(run.row, "object_type_api_name");
  const sourceRef = readRecord(run.row, "source_ref");
  const sourceVersionId =
    sourceRef && typeof sourceRef["dataset_version_id"] === "string"
      ? (sourceRef["dataset_version_id"] as string)
      : null;
  const dataset = sourceVersionId
    ? indexes.datasetByVersionId.get(sourceVersionId)
    : undefined;
  if (!apiName || !dataset) return;

  const objectNodeId = `objectType:${apiName}`;
  if (!nodes.has(objectNodeId)) {
    nodes.set(objectNodeId, {
      id: objectNodeId,
      kind: "object_type",
      label: apiName,
      detail: readString(run.row, "trigger_type"),
      datasetRef: null,
      namespace: null,
      name: apiName,
      datasetId: null,
      latestVersion: null,
      versionIds: [],
      isStale: false,
      buildStatus: runBuildStatus(run),
      resourceKind: "object_type",
    });
  }
  edges.push({
    id: `index-${run.runId ?? apiName}`,
    fromNodeId: datasetNodeId(datasetRefOf(dataset)),
    toNodeId: objectNodeId,
    relation: "index",
    createdByRunId: run.runId,
    fromVersionId: sourceVersionId,
    toVersionId: null,
  });
}

function applyTransformRun(
  run: FoundryLiteOperationsRunRow,
  indexes: DatasetIndexes,
  nodes: Map<string, LineageGraphNode>,
): void {
  const outputVersionId = readString(run.row, "output_version_id");
  const dataset = outputVersionId
    ? indexes.datasetByVersionId.get(outputVersionId)
    : undefined;
  if (!dataset) return;
  const nodeId = datasetNodeId(datasetRefOf(dataset));
  const node = nodes.get(nodeId);
  if (node) {
    nodes.set(nodeId, { ...node, buildStatus: runBuildStatus(run) });
  }
}

/**
 * datasets + versions + lineage edges + run rows를 화면 그래프 모델로 합성한다.
 * 순수 함수 — 네트워크 호출 없음.
 */
export function buildLineageGraphModel(
  source: LineageGraphSource,
  runRows: readonly FoundryLiteOperationsRunRow[],
): LineageGraphModel {
  const indexes = buildDatasetIndexes(source);
  const nodes = new Map<string, LineageGraphNode>();
  for (const dataset of source.datasets) {
    nodes.set(
      datasetNodeId(datasetRefOf(dataset)),
      createDatasetNode(dataset, indexes),
    );
  }

  const edges = applyLineageEdges(source, indexes, nodes);
  for (const run of runRows) {
    if (run.runType === "sync") applySyncRun(run, indexes, nodes, edges);
    if (run.runType === "index") applyIndexRun(run, indexes, nodes, edges);
    if (run.runType === "transform") applyTransformRun(run, indexes, nodes);
  }

  return {
    nodes: [...nodes.values()],
    edges,
    nodesById: nodes,
  };
}

/** 최장 경로 기반 레이어드 레이아웃 (좌→우). */
export function computeLineageLayout(
  model: LineageGraphModel,
): Map<string, { x: number; y: number }> {
  const rankByNodeId = new Map<string, number>();
  for (const node of model.nodes) rankByNodeId.set(node.id, 0);

  for (let pass = 0; pass < model.nodes.length; pass += 1) {
    let hasChanged = false;
    for (const edge of model.edges) {
      const fromRank = rankByNodeId.get(edge.fromNodeId) ?? 0;
      const toRank = rankByNodeId.get(edge.toNodeId) ?? 0;
      if (toRank < fromRank + 1) {
        rankByNodeId.set(edge.toNodeId, fromRank + 1);
        hasChanged = true;
      }
    }
    if (!hasChanged) break;
  }

  const nodesByRank = new Map<number, string[]>();
  for (const node of model.nodes) {
    const rank = rankByNodeId.get(node.id) ?? 0;
    nodesByRank.set(rank, [...(nodesByRank.get(rank) ?? []), node.id]);
  }

  const positions = new Map<string, { x: number; y: number }>();
  for (const [rank, nodeIds] of nodesByRank) {
    nodeIds.forEach((nodeId, index) => {
      positions.set(nodeId, {
        x: LAYOUT_X0 + rank * RANK_X_GAP,
        y: LAYOUT_Y0 + index * ROW_Y_GAP,
      });
    });
  }
  return positions;
}

export interface ImpactEntry {
  node: LineageGraphNode;
  depth: number;
}

/** 선택 노드 기준 downstream/upstream BFS (acceptance: 최소 1단계 impact). */
export function getImpactEntries(
  model: LineageGraphModel,
  startNodeId: string,
  direction: "downstream" | "upstream",
): ImpactEntry[] {
  const entries: ImpactEntry[] = [];
  const visited = new Set<string>([startNodeId]);
  let frontier = [startNodeId];
  let depth = 0;

  while (frontier.length > 0) {
    depth += 1;
    const nextFrontier: string[] = [];
    for (const edge of model.edges) {
      const [fromKey, toKey] =
        direction === "downstream"
          ? [edge.fromNodeId, edge.toNodeId]
          : [edge.toNodeId, edge.fromNodeId];
      if (!frontier.includes(fromKey) || visited.has(toKey)) continue;
      visited.add(toKey);
      const node = model.nodesById.get(toKey);
      if (node) {
        entries.push({ node, depth });
        nextFrontier.push(toKey);
      }
    }
    frontier = nextFrontier;
  }
  return entries;
}

/** run row가 노드와 관련 있는지 판정 (노드 선택 → run evidence 연결). */
export function isRunRelatedToNode(
  run: FoundryLiteOperationsRunRow,
  node: LineageGraphNode,
): boolean {
  if (node.kind === "object_type") {
    return (
      run.runType === "index" &&
      readString(run.row, "object_type_api_name") === node.name
    );
  }
  if (node.kind === "data_source") {
    return (
      run.runType === "sync" && readString(run.row, "sync_name") === node.name
    );
  }
  const versionIds = new Set(node.versionIds);
  if (run.runType === "transform") {
    const outputVersionId = readString(run.row, "output_version_id");
    if (outputVersionId && versionIds.has(outputVersionId)) return true;
    const inputVersions = readRecord(run.row, "input_versions");
    if (inputVersions && node.datasetRef && node.datasetRef in inputVersions)
      return true;
    const snapshot = readRecord(run.row, "definition_snapshot");
    return Boolean(
      snapshot && snapshot["output_dataset_ref"] === node.datasetRef,
    );
  }
  if (run.runType === "sync") {
    return readString(run.row, "output_dataset_id") === node.datasetId;
  }
  if (run.runType === "index") {
    const sourceRef = readRecord(run.row, "source_ref");
    const versionId =
      sourceRef && typeof sourceRef["dataset_version_id"] === "string"
        ? (sourceRef["dataset_version_id"] as string)
        : null;
    return Boolean(versionId && versionIds.has(versionId));
  }
  return false;
}

/** run history 테이블의 대상 리소스 라벨. */
export function readRunTargetLabel(
  run: FoundryLiteOperationsRunRow,
  datasetById: ReadonlyMap<string, Dataset>,
): string {
  if (run.runType === "transform") {
    const snapshot = readRecord(run.row, "definition_snapshot");
    const outputRef = snapshot?.["output_dataset_ref"];
    if (typeof outputRef === "string") return outputRef;
  }
  if (run.runType === "sync") {
    const datasetId = readString(run.row, "output_dataset_id");
    const dataset = datasetId ? datasetById.get(datasetId) : undefined;
    if (dataset) return datasetRefOf(dataset);
    return readString(run.row, "sync_name") ?? "—";
  }
  if (run.runType === "index") {
    return readString(run.row, "object_type_api_name") ?? "—";
  }
  return (
    readString(run.row, "event_type") ?? readString(run.row, "action") ?? "—"
  );
}

export function readRunTimestamp(
  run: FoundryLiteOperationsRunRow,
  key: string,
): string | null {
  return readString(run.row, key);
}

/** 컬러 모드에 따른 노드 컬러웨이. */
export function nodeColorway(
  node: LineageGraphNode,
  mode: LineageColorMode,
): NodeColorway {
  if (node.kind === "data_source") return NODE_COLORWAYS.sourceSlate;
  if (node.kind === "object_type") return NODE_COLORWAYS.objectTeal;

  if (mode === "build") {
    if (node.buildStatus === "failed") return NODE_COLORWAYS.failedRed;
    if (node.buildStatus === "running") return NODE_COLORWAYS.runningBlue;
    if (node.buildStatus === "success") return NODE_COLORWAYS.syncGreen;
    return NODE_COLORWAYS.neutralGray;
  }
  if (mode === "stale") {
    if (node.isStale) return NODE_COLORWAYS.staleTan;
    if (node.latestVersion) return NODE_COLORWAYS.upToDateBlue;
    return NODE_COLORWAYS.neutralGray;
  }
  if (node.isStale) return NODE_COLORWAYS.staleTan;
  return node.resourceKind === "transform_output"
    ? NODE_COLORWAYS.transformTan
    : NODE_COLORWAYS.syncGreen;
}

export const COLOR_MODE_LABELS: Record<LineageColorMode, string> = {
  resource: "리소스 유형",
  build: "빌드 상태",
  stale: "만료 여부",
};

export interface LegendItem {
  label: string;
  colorway: NodeColorway;
  count: number;
}

/** 현재 컬러 모드 기준 범례 항목 + 개수. */
export function buildLegendItems(
  model: LineageGraphModel,
  mode: LineageColorMode,
): LegendItem[] {
  const counts = new Map<string, LegendItem>();
  const labelFor = (node: LineageGraphNode): string => {
    if (node.kind === "data_source") return "데이터 소스";
    if (node.kind === "object_type") return "오브젝트 타입";
    if (mode === "build") {
      if (node.buildStatus === "failed") return "빌드 실패";
      if (node.buildStatus === "running") return "빌드 진행 중";
      if (node.buildStatus === "success") return "빌드 성공";
      return "빌드 이력 없음";
    }
    if (mode === "stale") {
      if (node.isStale) return "만료됨";
      return node.latestVersion ? "최신" : "버전 없음";
    }
    if (node.isStale) return "만료됨";
    return node.resourceKind === "transform_output"
      ? "트랜스폼 산출"
      : "동기화 소스";
  };

  for (const node of model.nodes) {
    const label = labelFor(node);
    const existing = counts.get(label);
    if (existing) {
      counts.set(label, { ...existing, count: existing.count + 1 });
    } else {
      counts.set(label, {
        label,
        colorway: nodeColorway(node, mode),
        count: 1,
      });
    }
  }
  return [...counts.values()];
}
