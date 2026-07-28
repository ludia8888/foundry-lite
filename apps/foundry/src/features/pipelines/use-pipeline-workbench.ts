import type {
  Dataset,
  PipelineBranch,
  PipelineGraphV2,
  PipelineNodeDescriptorPayload,
  PipelineNodeType,
  PipelineSchemaColumn,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  autoLayoutPositions,
  asGraph,
  canonicalizeGraphForSave,
  createDescriptorNode,
  createDatasetNode,
  createGraphEdge,
  deriveNodeSchemas,
  graphPositions,
  isReadOnlyPipelineNode,
  issuesByNodeId,
  nextNodeId,
  type NodePosition,
  normalizeGraphDoc,
  parseValidation,
  type PipelineCanvasGraph,
  type PipelineCanvasNode,
  type PipelineConnection,
  type PositionsByNodeId,
  trainedModelUsageNodeIds,
  validatePipelineConnection,
  withNodeConfigurationPatch,
  withLayoutPositions,
  withImportedTrainedModel,
  withSqlInputTemplate,
  withoutImportedTrainedModel,
} from "./pipeline-model";
import { useSafeQuery } from "./use-safe-query";

type PipelineGraphQueryData = {
  branch: PipelineBranch | null;
  validation: Record<string, unknown> | null;
  diff: Record<string, unknown> | null;
};

type GraphSnapshot = {
  doc: PipelineCanvasGraph;
  positions: PositionsByNodeId;
};

/** 로컬 undo/redo 스택 상한 (doc 스냅샷 최대 50개). */
const MAX_HISTORY = 50;

/** 그래프 편집 상태(로컬 문서) + 서버 브랜치/검증 상태를 묶는 워크벤치 훅. */
export function usePipelineWorkbench() {
  const client = useFoundryLiteClient();

  // useFoundryLiteProvidedPipelineBuilder와 동일 조합 (branches + proposals 병렬).
  const loadBuilder = useCallback(async () => {
    const [branches, proposals, nodeTypes] = await Promise.all([
      client.pipelines.branches.list({ limit: 50 }),
      client.pipelines.proposals.list({ limit: 50 }),
      client.pipelines.nodeTypes(),
    ]);
    return {
      branches: branches.items,
      proposals: proposals.items,
      nodeTypes: nodeTypes.items,
    };
  }, [client]);
  const builder = useSafeQuery(["pipelines", "builder", 50], loadBuilder);

  const loadDatasets = useCallback(() => client.datasets.list(), [client]);
  const datasetsQuery = useSafeQuery(
    ["pipelines", "dataset-catalog"],
    loadDatasets,
  );

  const branches = useMemo(() => builder.data?.branches ?? [], [builder.data]);
  const nodeDescriptors = useMemo(
    () => builder.data?.nodeTypes ?? [],
    [builder.data],
  );
  const descriptorById = useMemo(
    () => currentDescriptorsById(nodeDescriptors),
    [nodeDescriptors],
  );
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const branchId = selectedBranchId ?? branches[0]?.id ?? null;

  // useFoundryLitePipelineGraph와 동일 조합 (branch + validate + diff 병렬).
  const loadGraph = useCallback(async (): Promise<PipelineGraphQueryData> => {
    if (!branchId) return { branch: null, validation: null, diff: null };
    const [branch, validation, diff] = await Promise.all([
      client.pipelines.branches.get(branchId),
      client.pipelines.graph.validate(branchId),
      client.pipelines.branches.diff(branchId),
    ]);
    return { branch, validation, diff };
  }, [branchId, client]);
  const graphQuery = useSafeQuery(["pipelines", "graph", branchId], loadGraph, {
    enabled: Boolean(branchId),
  });
  const branch = graphQuery.data?.branch ?? null;
  const validation = useMemo(
    () => parseValidation(graphQuery.data?.validation ?? null),
    [graphQuery.data],
  );
  const diff = graphQuery.data?.diff ?? null;

  const [doc, setDoc] = useState<PipelineCanvasGraph | null>(null);
  const [positions, setPositions] = useState<PositionsByNodeId>({});
  const [isDirty, setIsDirty] = useState(false);
  const [connectionIssue, setConnectionIssue] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<readonly string[]>([]);
  const [syncedFingerprint, setSyncedFingerprint] = useState<string | null>(
    null,
  );

  // 로컬 undo/redo: 구조 변경(노드/엣지/데이터) 직전 doc+positions 스냅샷을 쌓는다 (서버 왕복 없음).
  const docRef = useRef<PipelineCanvasGraph | null>(null);
  const positionsRef = useRef<PositionsByNodeId>({});
  const syncedSnapshotRef = useRef<GraphSnapshot | null>(null);
  const undoStackRef = useRef<GraphSnapshot[]>([]);
  const redoStackRef = useRef<GraphSnapshot[]>([]);
  const [historyVersion, setHistoryVersion] = useState(0);

  useEffect(() => {
    docRef.current = doc;
  }, [doc]);
  useEffect(() => {
    positionsRef.current = positions;
  }, [positions]);

  const clearHistory = useCallback(() => {
    undoStackRef.current = [];
    redoStackRef.current = [];
    setHistoryVersion((version) => version + 1);
  }, []);

  const markDirtyForSnapshot = useCallback((snapshot: GraphSnapshot) => {
    setIsDirty(!snapshotsEqual(snapshot, syncedSnapshotRef.current));
  }, []);

  const pushHistory = useCallback(() => {
    const current = docRef.current;
    if (!current) return;
    undoStackRef.current = [
      ...undoStackRef.current.slice(-(MAX_HISTORY - 1)),
      { doc: current, positions: positionsRef.current },
    ];
    redoStackRef.current = [];
    setHistoryVersion((version) => version + 1);
  }, []);

  const handleUndo = useCallback(() => {
    const snapshot = undoStackRef.current.at(-1);
    const current = docRef.current;
    if (!snapshot || !current) return;
    undoStackRef.current = undoStackRef.current.slice(0, -1);
    redoStackRef.current = [
      ...redoStackRef.current,
      { doc: current, positions: positionsRef.current },
    ];
    setDoc(snapshot.doc);
    setPositions(snapshot.positions);
    markDirtyForSnapshot(snapshot);
    setSelectedNodeIds([]);
    setHistoryVersion((version) => version + 1);
  }, [markDirtyForSnapshot]);

  const handleRedo = useCallback(() => {
    const snapshot = redoStackRef.current.at(-1);
    const current = docRef.current;
    if (!snapshot || !current) return;
    redoStackRef.current = redoStackRef.current.slice(0, -1);
    undoStackRef.current = [
      ...undoStackRef.current.slice(-(MAX_HISTORY - 1)),
      { doc: current, positions: positionsRef.current },
    ];
    setDoc(snapshot.doc);
    setPositions(snapshot.positions);
    markDirtyForSnapshot(snapshot);
    setSelectedNodeIds([]);
    setHistoryVersion((version) => version + 1);
  }, [markDirtyForSnapshot]);

  // historyVersion을 의존해 렌더마다 최신 스택 길이를 반영한다.
  const canUndo = useMemo(
    () => undoStackRef.current.length > 0,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [historyVersion],
  );
  const canRedo = useMemo(
    () => redoStackRef.current.length > 0,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [historyVersion],
  );
  // 인스펙터/미리보기는 단일 선택일 때만 대상 노드를 갖는다 (다중 선택은 제거/정렬용).
  const selectedNodeId =
    selectedNodeIds.length === 1 ? selectedNodeIds[0] : null;
  const setSelectedNodeId = useCallback((nodeId: string | null) => {
    setSelectedNodeIds(nodeId ? [nodeId] : []);
  }, []);

  useEffect(() => {
    if (!branch) return;
    if (isDirty && syncedFingerprint === branch.graphFingerprint) return;
    const normalized = normalizeGraphDoc(
      asGraph(branch.graph),
      nodeDescriptors,
    );
    setDoc(normalized);
    const saved = graphPositions(normalized);
    const nextPositions =
      Object.keys(saved).length > 0 ? saved : autoLayoutPositions(normalized);
    setPositions(nextPositions);
    syncedSnapshotRef.current = { doc: normalized, positions: nextPositions };
    setIsDirty(false);
    setSyncedFingerprint(branch.graphFingerprint);
    clearHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branch?.id, branch?.graphFingerprint, nodeDescriptors]);

  const applyDoc = useCallback(
    (updater: (current: PipelineCanvasGraph) => PipelineCanvasGraph) => {
      const current = docRef.current;
      if (!current) return;
      pushHistory();
      const next = deriveNodeSchemas(updater(current));
      setDoc(next);
      markDirtyForSnapshot({ doc: next, positions: positionsRef.current });
    },
    [markDirtyForSnapshot, pushHistory],
  );

  const handleSelectBranch = useCallback(
    (nextBranchId: string) => {
      setSelectedBranchId(nextBranchId);
      setSelectedNodeIds([]);
      setIsDirty(false);
      setSyncedFingerprint(null);
      syncedSnapshotRef.current = null;
      clearHistory();
    },
    [clearHistory],
  );

  const appendNode = useCallback(
    (node: PipelineCanvasNode) => {
      const current = docRef.current;
      if (!current) return;
      pushHistory();
      const nextPositions = {
        ...positionsRef.current,
        [node.id]: nextFreePosition(positionsRef.current),
      };
      const nextDoc = { ...current, nodes: [...current.nodes, node] };
      setDoc(nextDoc);
      setPositions(nextPositions);
      setSelectedNodeIds([node.id]);
      markDirtyForSnapshot({ doc: nextDoc, positions: nextPositions });
    },
    [markDirtyForSnapshot, pushHistory],
  );

  /** 공식 툴바 '선택하기': 그래프의 모든 노드를 선택한다. */
  const handleSelectAllNodes = useCallback(() => {
    setDoc((current) => {
      if (current) setSelectedNodeIds(current.nodes.map((node) => node.id));
      return current;
    });
  }, []);

  const handleAddDatasetNode = useCallback(
    async (dataset: Dataset) => {
      if (!doc) return;
      // 배포 컴파일러는 도트 형식(namespace.name)만 허용한다.
      const datasetRef = `${dataset.namespace}.${dataset.name}`;
      let schema: PipelineSchemaColumn[] = [];
      try {
        const inspection = await client.datasets.inspect(
          dataset.namespace,
          dataset.name,
        );
        schema = schemaColumnsOf(inspection.schema);
      } catch {
        schema = [];
      }
      const id = nextNodeId(
        "dataset",
        doc.nodes.map((node) => node.id),
      );
      appendNode(
        createDatasetNode(
          id,
          datasetRef,
          schema,
          descriptorById.get("source.dataset"),
        ),
      );
    },
    [appendNode, client, descriptorById, doc],
  );

  const handleAddTransformNode = useCallback(
    (type: PipelineNodeType) => {
      if (!doc) return;
      const descriptorId = legacyDescriptorId(type);
      const descriptor = descriptorById.get(descriptorId);
      if (!descriptor) return;
      const id = nextNodeId(
        descriptorId,
        doc.nodes.map((node) => node.id),
      );
      const node = createDescriptorNode(
        id,
        descriptor,
        branch?.pipelineId ?? "pipeline",
      );
      if (node) appendNode(node);
    },
    [appendNode, branch?.pipelineId, descriptorById, doc],
  );

  const handleAddDescriptorNode = useCallback(
    (descriptor: PipelineNodeDescriptorPayload) => {
      if (!doc) return;
      const id = nextNodeId(
        descriptor.descriptorId,
        doc.nodes.map((node) => node.id),
      );
      const node = createDescriptorNode(
        id,
        descriptor,
        branch?.pipelineId ?? "pipeline",
      );
      if (node) appendNode(node);
    },
    [appendNode, branch?.pipelineId, doc],
  );

  const handleImportTrainedModel = useCallback(
    (modelRef: string) => {
      setConnectionIssue(null);
      applyDoc((current) => withImportedTrainedModel(current, modelRef));
    },
    [applyDoc],
  );

  const handleRemoveTrainedModel = useCallback(
    (modelRef: string) => {
      const usageNodeIds = trainedModelUsageNodeIds(docRef.current, modelRef);
      if (usageNodeIds.length > 0) {
        setConnectionIssue(
          `사용 중인 Trained Model은 Reusables에서 제거할 수 없습니다: ${usageNodeIds.join(", ")}`,
        );
        return;
      }
      setConnectionIssue(null);
      applyDoc((current) => withoutImportedTrainedModel(current, modelRef));
    },
    [applyDoc],
  );

  const handleConnectNodes = useCallback(
    (connection: PipelineConnection) => {
      const current = docRef.current;
      if (!current) return;
      const validation = validatePipelineConnection(current, connection);
      if (!validation.isValid) {
        setConnectionIssue(validation.reason);
        return;
      }
      setConnectionIssue(null);
      applyDoc((current) => {
        const withEdge = {
          ...current,
          edges: [
            ...current.edges,
            createGraphEdge(validation.connection, current.edges),
          ],
        };
        // sql 노드 연결 시 기본 SQL 템플릿의 입력 참조를 실제 upstream ref로 채운다.
        return withSqlInputTemplate(
          withEdge,
          validation.connection.source,
          validation.connection.target,
        );
      });
    },
    [applyDoc],
  );

  /** 엣지 중간 ⊕: 두 노드 사이에 변환 노드를 삽입하고 엣지를 재배선한다. */
  const handleInsertNodeBetween = useCallback(
    (edgeId: string, type: PipelineNodeType) => {
      const current = docRef.current;
      if (!current) return;
      const edge = current.edges.find(
        (item) => (item.id ?? `${item.source}->${item.target}`) === edgeId,
      );
      if (!edge) return;
      const edgeSource = current.nodes.find((node) => node.id === edge.source);
      const edgeTarget = current.nodes.find((node) => node.id === edge.target);
      if (
        isReadOnlyPipelineNode(edgeSource) ||
        isReadOnlyPipelineNode(edgeTarget)
      ) {
        setConnectionIssue(
          "읽기 전용 Graph v2 노드에 연결된 edge에는 변환을 삽입할 수 없습니다.",
        );
        return;
      }
      const descriptor = descriptorById.get(legacyDescriptorId(type));
      if (!descriptor) return;
      const id = nextNodeId(
        descriptor.descriptorId,
        current.nodes.map((node) => node.id),
      );
      const node = createDescriptorNode(
        id,
        descriptor,
        branch?.pipelineId ?? "pipeline",
      );
      if (!node) return;
      const remainingEdges = current.edges.filter((item) => item !== edge);
      const inboundEdge = createGraphEdge(
        {
          source: edge.source,
          target: id,
          sourceHandle: edge.sourceHandle ?? null,
          targetHandle: type === "join" ? "left" : null,
        },
        remainingEdges,
      );
      const outboundEdge = createGraphEdge(
        {
          source: id,
          target: edge.target,
          targetHandle: edge.targetHandle ?? null,
        },
        [...remainingEdges, inboundEdge],
      );
      const rewired: PipelineCanvasGraph = {
        ...current,
        nodes: [...current.nodes, node],
        edges: [...remainingEdges, inboundEdge, outboundEdge],
      };
      const nextDoc = withSqlInputTemplate(rewired, edge.source, id);
      pushHistory();
      const source = positionsRef.current[edge.source];
      const target = positionsRef.current[edge.target];
      const midpoint =
        source && target
          ? { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 }
          : nextFreePosition(positionsRef.current);
      const nextPositions = { ...positionsRef.current, [id]: midpoint };
      setDoc(nextDoc);
      setPositions(nextPositions);
      setSelectedNodeIds([id]);
      markDirtyForSnapshot({ doc: nextDoc, positions: nextPositions });
    },
    [branch?.pipelineId, descriptorById, markDirtyForSnapshot, pushHistory],
  );

  const handleDeleteNodes = useCallback(
    (nodeIds: readonly string[]) => {
      const currentDoc = docRef.current;
      if (!currentDoc) return;
      const requested = new Set(nodeIds);
      const readOnlyIds = new Set(
        currentDoc.nodes
          .filter(isReadOnlyPipelineNode)
          .map((node) => node.id),
      );
      const protectedIds = new Set(
        currentDoc.nodes
        .filter(
            (node) =>
              requested.has(node.id) &&
              (readOnlyIds.has(node.id) ||
                currentDoc.edges.some(
                  (edge) =>
                    (edge.source === node.id &&
                      readOnlyIds.has(edge.target)) ||
                    (edge.target === node.id &&
                      readOnlyIds.has(edge.source)),
                )),
        )
          .map((node) => node.id),
      );
      const removed = new Set(
        nodeIds.filter((nodeId) => !protectedIds.has(nodeId)),
      );
      if (protectedIds.size > 0) {
        setConnectionIssue(
          `읽기 전용 Graph v2 계약 또는 그 named-port 연결을 훼손하는 노드는 삭제할 수 없습니다: ${[...protectedIds].join(", ")}`,
        );
        return;
      }
      if (removed.size === 0) return;
      applyDoc((current) => ({
        ...current,
        nodes: current.nodes.filter((node) => !removed.has(node.id)),
        edges: current.edges.filter(
          (edge) => !removed.has(edge.source) && !removed.has(edge.target),
        ),
      }));
      setSelectedNodeIds((current) =>
        current.filter((nodeId) => !removed.has(nodeId)),
      );
    },
    [applyDoc],
  );

  const handleDeleteEdges = useCallback(
    (edgeIds: readonly string[]) => {
      const currentDoc = docRef.current;
      if (!currentDoc) return;
      const requested = new Set(edgeIds);
      const readOnlyNodeIds = new Set(
        currentDoc.nodes
          .filter(isReadOnlyPipelineNode)
          .map((node) => node.id),
      );
      const protectedEdgeIds = currentDoc.edges
        .filter(
          (edge) =>
            requested.has(edge.id ?? `${edge.source}->${edge.target}`) &&
            (readOnlyNodeIds.has(edge.source) ||
              readOnlyNodeIds.has(edge.target)),
        )
        .map((edge) => edge.id ?? `${edge.source}->${edge.target}`);
      const removed = new Set(
        edgeIds.filter((edgeId) => !protectedEdgeIds.includes(edgeId)),
      );
      if (protectedEdgeIds.length > 0) {
        setConnectionIssue(
          "읽기 전용 Graph v2 노드의 기존 named-port 연결은 삭제할 수 없습니다.",
        );
      }
      if (removed.size === 0) return;
      applyDoc((current) => ({
        ...current,
        edges: current.edges.filter(
          (edge) => !removed.has(edge.id ?? `${edge.source}->${edge.target}`),
        ),
      }));
    },
    [applyDoc],
  );

  const handleMoveNode = useCallback(
    (nodeId: string, position: NodePosition) => {
      const current = docRef.current;
      const nextPositions = { ...positionsRef.current, [nodeId]: position };
      setPositions(nextPositions);
      if (current) {
        markDirtyForSnapshot({ doc: current, positions: nextPositions });
      }
    },
    [markDirtyForSnapshot],
  );

  const handleUpdateNodeData = useCallback(
    (nodeId: string, patch: Record<string, unknown>) => {
      const node = docRef.current?.nodes.find((item) => item.id === nodeId);
      if (isReadOnlyPipelineNode(node)) {
        setConnectionIssue(
          "이 Graph v2 descriptor는 현재 UI에서 읽기 전용이며 원본 설정을 그대로 보존합니다.",
        );
        return;
      }
      applyDoc((current) => ({
        ...current,
        nodes: current.nodes.map((node) =>
          node.id === nodeId
            ? withNodeConfigurationPatch(node, patch)
            : node,
        ),
      }));
    },
    [applyDoc],
  );

  const handleAutoLayout = useCallback(() => {
    const current = docRef.current;
    if (!current) return;
    pushHistory();
    const nextPositions = autoLayoutPositions(current);
    setPositions(nextPositions);
    markDirtyForSnapshot({ doc: current, positions: nextPositions });
  }, [markDirtyForSnapshot, pushHistory]);

  const buildGraphForSave = useCallback((): PipelineGraphV2 | null => {
    if (!doc) return null;
    return canonicalizeGraphForSave(
      withLayoutPositions(deriveNodeSchemas(doc), positions),
    );
  }, [doc, positions]);

  const handleGraphSaved = useCallback((saved: PipelineBranch) => {
    const normalized = normalizeGraphDoc(
      asGraph(saved.graph),
      nodeDescriptors,
    );
    const savedPositions = graphPositions(normalized);
    const nextPositions =
      Object.keys(savedPositions).length > 0
        ? savedPositions
        : positionsRef.current;
    setDoc(normalized);
    setPositions(nextPositions);
    syncedSnapshotRef.current = { doc: normalized, positions: nextPositions };
    setIsDirty(false);
    setSyncedFingerprint(saved.graphFingerprint);
  }, [nodeDescriptors]);

  const selectedNode: PipelineCanvasNode | null =
    doc?.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const nodeIssues = useMemo(
    () => issuesByNodeId(validation?.errors ?? []),
    [validation],
  );

  return {
    client,
    builder,
    nodeDescriptors,
    datasetsQuery,
    branches,
    branchId,
    branch,
    graphQuery,
    validation,
    diff,
    doc,
    positions,
    isDirty,
    connectionIssue,
    selectedNodeId,
    selectedNodeIds,
    selectedNode,
    nodeIssues,
    setSelectedNodeId,
    setSelectedNodeIds,
    handleSelectAllNodes,
    handleSelectBranch,
    handleAddDatasetNode,
    handleAddTransformNode,
    handleAddDescriptorNode,
    handleImportTrainedModel,
    handleRemoveTrainedModel,
    handleConnectNodes,
    handleInsertNodeBetween,
    handleDeleteNodes,
    handleDeleteEdges,
    handleMoveNode,
    handleUpdateNodeData,
    handleAutoLayout,
    handleUndo,
    handleRedo,
    canUndo,
    canRedo,
    buildGraphForSave,
    handleGraphSaved,
  };
}

export type PipelineWorkbench = ReturnType<typeof usePipelineWorkbench>;

function schemaColumnsOf(
  schema: Record<string, unknown>,
): PipelineSchemaColumn[] {
  const columns = schema.columns;
  if (!Array.isArray(columns)) return [];
  return columns.filter(
    (column): column is PipelineSchemaColumn =>
      typeof column === "object" &&
      column !== null &&
      typeof (column as { name?: unknown }).name === "string" &&
      typeof (column as { type?: unknown }).type === "string",
  );
}

function nextFreePosition(positions: PositionsByNodeId): NodePosition {
  const count = Object.keys(positions).length;
  return { x: 60 + (count % 4) * 280, y: 60 + Math.floor(count / 4) * 140 };
}

function snapshotsEqual(
  left: GraphSnapshot | null,
  right: GraphSnapshot | null,
): boolean {
  if (!left || !right) return false;
  return (
    stableStringify(snapshotGraph(left)) ===
    stableStringify(snapshotGraph(right))
  );
}

function snapshotGraph(snapshot: GraphSnapshot): PipelineCanvasGraph {
  return withLayoutPositions(snapshot.doc, snapshot.positions);
}

function legacyDescriptorId(type: PipelineNodeType): string {
  const descriptorByType: Record<PipelineNodeType, string> = {
    dataset: "source.dataset",
    sql: "transform.sql",
    python: "transform.python",
    join: "transform.join",
    union: "transform.union",
    select_cast: "transform.select_cast",
    output_dataset: "output.dataset",
  };
  return descriptorByType[type];
}

function currentDescriptorsById(
  descriptors: readonly PipelineNodeDescriptorPayload[],
): Map<string, PipelineNodeDescriptorPayload> {
  const result = new Map<string, PipelineNodeDescriptorPayload>();
  for (const descriptor of descriptors) {
    const current = result.get(descriptor.descriptorId);
    if (!current || descriptor.specVersion > current.specVersion) {
      result.set(descriptor.descriptorId, descriptor);
    }
  }
  return result;
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(
        ([key]) =>
          ![
            "descriptor",
            "rawV2Node",
            "rawV2Edge",
            "rawV2Graph",
          ].includes(key),
      )
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}
