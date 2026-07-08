import type { PipelineGraph, PipelineNodeType } from "@foundry-lite/sdk";
import {
  Panel,
  ReactFlow,
  SelectionMode,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import { ZoomIn, ZoomOut } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type {
  NodePosition,
  PipelineValidationIssue,
  PositionsByNodeId,
} from "../pipeline-model";
import {
  InsertTransformEdge,
  type PipelineFlowEdge,
} from "./InsertTransformEdge";
import { PipelineNodeCard, type PipelineFlowNode } from "./PipelineNodeCard";

import "@xyflow/react/dist/style.css";

const NODE_TYPES = { pipelineNode: PipelineNodeCard };
const EDGE_TYPES = { insertEdge: InsertTransformEdge };

export type CanvasInteractionMode = "pan" | "select";

/** 출력 목록 등 외부에서 특정 노드로 뷰포트를 이동시키는 신호 (seq로 재발동). */
export type CanvasFocusSignal = { nodeId: string; seq: number };

interface PipelineFlowCanvasProps {
  graph: PipelineGraph;
  positions: PositionsByNodeId;
  selectedNodeIds: readonly string[];
  interactionMode: CanvasInteractionMode;
  nodeIssues: Record<string, PipelineValidationIssue[]>;
  focusSignal: CanvasFocusSignal | null;
  onChangeSelection: (nodeIds: readonly string[]) => void;
  onMoveNode: (nodeId: string, position: NodePosition) => void;
  onConnectNodes: (source: string, target: string) => void;
  onDeleteNodes: (nodeIds: readonly string[]) => void;
  onDeleteEdges: (edgeIds: readonly string[]) => void;
  onInsertTransform: (edgeId: string, type: PipelineNodeType) => void;
}

/** 민무늬 #EEF1F4 캔버스 위 @xyflow 그래프. 노드/엣지는 워크벤치 doc에서 파생되는 controlled 구성. */
export function PipelineFlowCanvas({
  graph,
  positions,
  selectedNodeIds,
  interactionMode,
  nodeIssues,
  focusSignal,
  onChangeSelection,
  onMoveNode,
  onConnectNodes,
  onDeleteNodes,
  onDeleteEdges,
  onInsertTransform,
}: PipelineFlowCanvasProps) {
  const instanceRef = useRef<ReactFlowInstance<
    PipelineFlowNode,
    PipelineFlowEdge
  > | null>(null);

  const selectedIdSet = useMemo(
    () => new Set(selectedNodeIds),
    [selectedNodeIds],
  );

  const nodes = useMemo<PipelineFlowNode[]>(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: "pipelineNode" as const,
        position: positions[node.id] ?? { x: 60, y: 60 },
        selected: selectedIdSet.has(node.id),
        data: { node, errorCount: nodeIssues[node.id]?.length ?? 0 },
      })),
    [graph.nodes, positions, selectedIdSet, nodeIssues],
  );

  // 조인 노드로 들어가는 엣지는 등장 순서대로 좌/우 타깃 핸들에 붙인다 (공식 노드 카드 구조).
  const edges = useMemo<PipelineFlowEdge[]>(() => {
    const joinIds = new Set(
      graph.nodes.filter((node) => node.type === "join").map((node) => node.id),
    );
    const joinInputCounts: Record<string, number> = {};
    return graph.edges.map((edge) => {
      let targetHandle: string | undefined;
      if (joinIds.has(edge.target)) {
        const index = joinInputCounts[edge.target] ?? 0;
        joinInputCounts[edge.target] = index + 1;
        targetHandle = index === 0 ? "left" : "right";
      }
      return {
        id: edge.id ?? `${edge.source}->${edge.target}`,
        source: edge.source,
        target: edge.target,
        targetHandle,
        type: "insertEdge" as const,
        data: { onInsertTransform },
      };
    });
  }, [graph.edges, graph.nodes, onInsertTransform]);

  useEffect(() => {
    if (!focusSignal) return;
    void instanceRef.current?.fitView({
      nodes: [{ id: focusSignal.nodeId }],
      duration: 300,
      maxZoom: 1.1,
      padding: 0.4,
    });
  }, [focusSignal]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<PipelineFlowNode>[]) => {
      const removedIds: string[] = [];
      let nextSelection: Set<string> | null = null;
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          onMoveNode(change.id, change.position);
        } else if (change.type === "select") {
          nextSelection ??= new Set(selectedIdSet);
          if (change.selected) nextSelection.add(change.id);
          else nextSelection.delete(change.id);
        } else if (change.type === "remove") {
          removedIds.push(change.id);
        }
      }
      if (nextSelection) onChangeSelection([...nextSelection]);
      if (removedIds.length > 0) onDeleteNodes(removedIds);
    },
    [onMoveNode, onChangeSelection, onDeleteNodes, selectedIdSet],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<PipelineFlowEdge>[]) => {
      const removedIds = changes
        .filter((change) => change.type === "remove")
        .map((change) => change.id);
      if (removedIds.length > 0) onDeleteEdges(removedIds);
    },
    [onDeleteEdges],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        onConnectNodes(connection.source, connection.target);
      }
    },
    [onConnectNodes],
  );

  const isSelectMode = interactionMode === "select";

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      onInit={(instance) => {
        instanceRef.current = instance;
      }}
      onNodesChange={handleNodesChange}
      onEdgesChange={handleEdgesChange}
      onConnect={handleConnect}
      onPaneClick={() => onChangeSelection([])}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
      deleteKeyCode={["Backspace", "Delete"]}
      className="!bg-[#EEF1F4]"
      minZoom={0.3}
      maxZoom={1.75}
      panOnDrag={isSelectMode ? [1, 2] : true}
      selectionOnDrag={isSelectMode}
      selectionMode={SelectionMode.Partial}
      proOptions={{ hideAttribution: true }}
    >
      {/* 공식 캔버스 우하단 돋보기 줌 컨트롤 */}
      <Panel position="bottom-right" className="!m-2">
        <div className="flex flex-col overflow-hidden rounded border border-[#C5CBD3] bg-white shadow-sm">
          <button
            type="button"
            title="확대"
            className="flex size-7 items-center justify-center hover:bg-muted"
            onClick={() => void instanceRef.current?.zoomIn({ duration: 150 })}
          >
            <ZoomIn className="size-4 text-[#5F6B7C]" />
          </button>
          <button
            type="button"
            title="축소"
            className="flex size-7 items-center justify-center border-t border-[#C5CBD3] hover:bg-muted"
            onClick={() => void instanceRef.current?.zoomOut({ duration: 150 })}
          >
            <ZoomOut className="size-4 text-[#5F6B7C]" />
          </button>
        </div>
      </Panel>
    </ReactFlow>
  );
}
