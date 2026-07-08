import type { Edge, NodeMouseHandler } from "@xyflow/react";
import {
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import {
  ChevronDown,
  ChevronRight,
  LayoutGrid,
  Plus,
  Search,
  Table2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import "@xyflow/react/dist/style.css";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import type { ExploreFkEdge, ExploreTable } from "../explore-model";
import {
  computeExploreGraphPositions,
  fkColumnNamesByTable,
} from "../explore-model";
import type { ExploreFlowNode } from "./ExploreTableNode";
import { ExploreTableNode } from "./ExploreTableNode";

const NODE_TYPES = { exploreTable: ExploreTableNode };
const EDGE_GRAY = "#5f6b7c";
const EDGE_ORANGE = "#c87619";

interface ExploreGraphViewProps {
  tables: ExploreTable[];
  fkEdges: ExploreFkEdge[];
  activeTable: string | null;
  selectedTables: readonly string[];
  onPreviewTable: (tableName: string) => void;
  onToggleSelect: (tableName: string) => void;
  onCreateSyncForTable: (tableName: string) => void;
}

interface ContextMenuState {
  tableName: string;
  x: number;
  y: number;
}

/**
 * 하단 접이식 "그래프 뷰" (공식 db-explorer): 테이블 노드 + 컬럼명 기반
 * FK 추정 엣지 + 우클릭 "이 테이블 동기화 생성" 컨텍스트 메뉴 (bg-canvas).
 */
export function ExploreGraphView(props: ExploreGraphViewProps) {
  const [isOpen, setIsOpen] = useState(true);
  return (
    <div className="shrink-0 border-t">
      <button
        type="button"
        className="flex h-8 w-full items-center gap-1.5 bg-muted/30 px-2 text-xs font-medium hover:bg-accent/50"
        onClick={() => setIsOpen((current) => !current)}
      >
        {isOpen ? (
          <ChevronDown className="size-3.5" />
        ) : (
          <ChevronRight className="size-3.5" />
        )}
        그래프 뷰
        <span className="font-normal text-muted-foreground">
          테이블 {props.tables.length}개 · FK 추정 {props.fkEdges.length}개
        </span>
      </button>
      {isOpen ? (
        <ReactFlowProvider>
          <ExploreGraphCanvas {...props} />
        </ReactFlowProvider>
      ) : null}
    </div>
  );
}

function useExploreFlowElements({
  tables,
  fkEdges,
  activeTable,
  selectedTables,
  onToggleSelect,
}: Pick<
  ExploreGraphViewProps,
  "tables" | "fkEdges" | "activeTable" | "selectedTables" | "onToggleSelect"
>): { flowNodes: ExploreFlowNode[]; flowEdges: Edge[] } {
  return useMemo(() => {
    const positions = computeExploreGraphPositions(tables);
    const fkNamesByTable = fkColumnNamesByTable(fkEdges);
    const selectedSet = new Set(selectedTables);

    const flowNodes: ExploreFlowNode[] = tables.map((table) => ({
      id: table.tableName,
      type: "exploreTable" as const,
      position: positions.get(table.tableName) ?? { x: 0, y: 0 },
      data: {
        tableName: table.tableName,
        columns: table.columns,
        fkColumnNames: fkNamesByTable.get(table.tableName) ?? [],
        isSelected: selectedSet.has(table.tableName),
        isActive: table.tableName === activeTable,
        onToggleSelect,
      },
    }));

    const flowEdges: Edge[] = fkEdges.map((edge) => {
      const isHighlighted =
        edge.sourceTable === activeTable || edge.targetTable === activeTable;
      const color = isHighlighted ? EDGE_ORANGE : EDGE_GRAY;
      return {
        id: edge.id,
        source: edge.sourceTable,
        target: edge.targetTable,
        label: `${edge.columnName} <> ${edge.targetTable}`,
        labelStyle: { fontSize: 9, fill: color, fontFamily: "monospace" },
        style: { stroke: color, strokeWidth: isHighlighted ? 1.8 : 1.2 },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14 },
      };
    });

    return { flowNodes, flowEdges };
  }, [tables, fkEdges, activeTable, selectedTables, onToggleSelect]);
}

function ExploreGraphCanvas({
  tables,
  fkEdges,
  activeTable,
  selectedTables,
  onPreviewTable,
  onToggleSelect,
  onCreateSyncForTable,
}: ExploreGraphViewProps) {
  const { flowNodes, flowEdges } = useExploreFlowElements({
    tables,
    fkEdges,
    activeTable,
    selectedTables,
    onToggleSelect,
  });
  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes);
  const [edges, setEdges] = useEdgesState(flowEdges);
  const [searchText, setSearchText] = useState("");
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const { fitView } = useReactFlow();

  useEffect(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  const handleSearch = (value: string) => {
    setSearchText(value);
    const query = value.trim().toLowerCase();
    if (!query) return;
    const match = tables.find((table) =>
      table.tableName.toLowerCase().includes(query),
    );
    if (match) {
      void fitView({ nodes: [{ id: match.tableName }], duration: 300 });
    }
  };

  const handleNodeContextMenu: NodeMouseHandler<ExploreFlowNode> = (
    event,
    node,
  ) => {
    event.preventDefault();
    const bounds = wrapperRef.current?.getBoundingClientRect();
    if (!bounds) return;
    setContextMenu({
      tableName: node.id,
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    });
  };

  return (
    <div ref={wrapperRef} className="relative h-72">
      <div className="absolute top-2 left-2 z-10 flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="h-7 bg-card text-xs"
          onClick={() => void fitView({ padding: 0.25, duration: 300 })}
        >
          <LayoutGrid className="size-3.5" /> 레이아웃
        </Button>
        <div className="relative">
          <Search className="absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchText}
            onChange={(event) => handleSearch(event.target.value)}
            placeholder="검색..."
            className="h-7 w-36 bg-card pl-7 text-xs"
          />
        </div>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onNodeClick={(_event, node) => onPreviewTable(node.id)}
        onNodeContextMenu={handleNodeContextMenu}
        onPaneClick={() => setContextMenu(null)}
        onMoveStart={() => setContextMenu(null)}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        minZoom={0.3}
        maxZoom={2}
        className="bg-canvas"
        deleteKeyCode={null}
        proOptions={{ hideAttribution: true }}
      />
      {contextMenu ? (
        <div
          className="absolute z-20 w-56 rounded border bg-card py-1 shadow-md"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          role="menu"
        >
          <div className="border-b px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
            {contextMenu.tableName}
          </div>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-accent"
            onClick={() => {
              onCreateSyncForTable(contextMenu.tableName);
              setContextMenu(null);
            }}
          >
            <Plus className="size-3.5 text-primary" /> 이 테이블 동기화 생성
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-accent"
            onClick={() => {
              onPreviewTable(contextMenu.tableName);
              setContextMenu(null);
            }}
          >
            <Table2 className="size-3.5 text-primary" /> 테이블 미리보기
          </button>
        </div>
      ) : null}
    </div>
  );
}
