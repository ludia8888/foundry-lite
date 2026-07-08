import {
  MarkerType,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import { Crosshair, Maximize, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useMemo } from "react";

import "@xyflow/react/dist/style.css";

import { Button } from "@/components/ui/button";
import type { LineageFlowNode } from "@/features/lineage/LineageDatasetNode";
import { LineageDatasetNode } from "@/features/lineage/LineageDatasetNode";
import type {
  LineageColorMode,
  LineageGraphModel,
} from "@/features/lineage/lineage-model";
import {
  EDGE_GRAY,
  EDGE_ORANGE,
  SELECTION_ORANGE,
  buildLegendItems,
  computeLineageLayout,
  getImpactEntries,
  nodeColorway,
} from "@/features/lineage/lineage-model";

const NODE_TYPES = { lineage: LineageDatasetNode };

interface LineageCanvasProps {
  model: LineageGraphModel;
  colorMode: LineageColorMode;
  isLegendVisible: boolean;
  selectedNodeId: string | null;
  layoutSeed: number;
  onSelectNode: (nodeId: string | null) => void;
  onSelectRun: (runId: string | null) => void;
}

function useLineageFlowElements(
  model: LineageGraphModel,
  colorMode: LineageColorMode,
  selectedNodeId: string | null,
  layoutSeed: number,
): { flowNodes: LineageFlowNode[]; flowEdges: Edge[] } {
  return useMemo(() => {
    const positions = computeLineageLayout(model);
    const downstreamIds = new Set(
      selectedNodeId
        ? getImpactEntries(model, selectedNodeId, "downstream").map(
            (entry) => entry.node.id,
          )
        : [],
    );
    const syncedNodeIds = new Set(
      model.edges
        .filter((edge) => edge.relation === "sync")
        .map((edge) => edge.toNodeId),
    );

    const flowNodes: LineageFlowNode[] = model.nodes.map((node) => ({
      id: node.id,
      type: "lineage" as const,
      position: positions.get(node.id) ?? { x: 0, y: 0 },
      selected: node.id === selectedNodeId,
      data: {
        model: node,
        colorway: nodeColorway(node, colorMode),
        isImpactHighlighted: downstreamIds.has(node.id),
        hasSyncBadge: syncedNodeIds.has(node.id),
      },
    }));

    const flowEdges: Edge[] = model.edges.map((edge) => {
      const isHighlighted =
        selectedNodeId !== null &&
        (edge.fromNodeId === selectedNodeId ||
          downstreamIds.has(edge.toNodeId)) &&
        (edge.fromNodeId === selectedNodeId ||
          downstreamIds.has(edge.fromNodeId));
      const color = isHighlighted ? EDGE_ORANGE : EDGE_GRAY;
      return {
        id: edge.id,
        source: edge.fromNodeId,
        target: edge.toNodeId,
        style: { stroke: color, strokeWidth: isHighlighted ? 2 : 1.4 },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color,
          width: 16,
          height: 16,
        },
        data: { createdByRunId: edge.createdByRunId },
      };
    });

    return { flowNodes, flowEdges };
    // layoutSeed는 재정렬 트리거 — 드래그된 위치를 초기 레이아웃으로 리셋한다.
  }, [model, colorMode, selectedNodeId, layoutSeed]);
}

function ZoomControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const controls = [
    {
      label: "선택 노드로 이동",
      icon: Crosshair,
      onClick: () => void fitView({ duration: 300 }),
    },
    { label: "확대", icon: ZoomIn, onClick: () => void zoomIn() },
    { label: "축소", icon: ZoomOut, onClick: () => void zoomOut() },
    {
      label: "전체 보기",
      icon: Maximize,
      onClick: () => void fitView({ padding: 0.2, duration: 300 }),
    },
  ];
  return (
    <div className="flex flex-col overflow-hidden rounded border bg-card shadow-sm">
      {controls.map((control) => (
        <Button
          key={control.label}
          variant="ghost"
          size="icon"
          className="size-8 rounded-none border-b last:border-b-0"
          title={control.label}
          onClick={control.onClick}
        >
          <control.icon className="size-3.5" />
        </Button>
      ))}
    </div>
  );
}

function LegendPanel({
  model,
  colorMode,
}: {
  model: LineageGraphModel;
  colorMode: LineageColorMode;
}) {
  const items = buildLegendItems(model, colorMode);
  if (items.length === 0) return null;
  return (
    <div className="grid grid-cols-2 gap-x-5 gap-y-1.5 rounded border bg-card p-3 shadow-sm">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2 text-xs">
          <span
            className="size-3 rounded-[2px] border"
            style={{
              background: item.colorway.background,
              borderColor: item.colorway.border,
            }}
          />
          <span>
            {item.label} ({item.count})
          </span>
        </div>
      ))}
    </div>
  );
}

function LineageFlow({
  model,
  colorMode,
  isLegendVisible,
  selectedNodeId,
  layoutSeed,
  onSelectNode,
  onSelectRun,
}: LineageCanvasProps) {
  const { flowNodes, flowEdges } = useLineageFlowElements(
    model,
    colorMode,
    selectedNodeId,
    layoutSeed,
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes);
  const [edges, setEdges] = useEdgesState(flowEdges);
  const { fitView } = useReactFlow();

  useEffect(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  useEffect(() => {
    if (layoutSeed > 0) {
      window.requestAnimationFrame(
        () => void fitView({ padding: 0.2, duration: 300 }),
      );
    }
  }, [layoutSeed, fitView]);

  const handleNodeClick: NodeMouseHandler<LineageFlowNode> = (_event, node) => {
    onSelectNode(node.id);
  };

  const handleEdgeClick = (_event: React.MouseEvent, edge: Edge) => {
    const runId = (edge.data as { createdByRunId?: string | null } | undefined)
      ?.createdByRunId;
    onSelectRun(runId ?? null);
  };

  const selectedCount = selectedNodeId ? 1 : 0;

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      onNodesChange={onNodesChange}
      onNodeClick={handleNodeClick}
      onEdgeClick={handleEdgeClick}
      onPaneClick={() => onSelectNode(null)}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.3}
      maxZoom={2}
      className="bg-canvas"
      deleteKeyCode={null}
    >
      <Panel position="bottom-left">
        <ZoomControls />
      </Panel>
      {isLegendVisible ? (
        <Panel position="top-right">
          <LegendPanel model={model} colorMode={colorMode} />
        </Panel>
      ) : null}
      {selectedCount > 0 ? (
        <Panel position="bottom-right">
          <span
            className="text-xs font-semibold"
            style={{ color: SELECTION_ORANGE }}
          >
            {selectedCount}개 노드 선택됨
          </span>
        </Panel>
      ) : null}
    </ReactFlow>
  );
}

/** 리니지 그래프 캔버스: 플랫 컬러 노드 + 오렌지 선택 하이라이트 + 범례/줌 (bg-canvas). */
export function LineageCanvas(props: LineageCanvasProps) {
  return (
    <ReactFlowProvider>
      <LineageFlow {...props} />
    </ReactFlowProvider>
  );
}
