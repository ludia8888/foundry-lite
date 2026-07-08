import { Cable, Waypoints } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Button } from "@/components/ui/button";
import {
  LineageBottomDock,
  type BottomDockTab,
} from "@/features/lineage/LineageBottomDock";
import { LineageCanvas } from "@/features/lineage/LineageCanvas";
import {
  LineageRightPanel,
  type RightPanelTab,
} from "@/features/lineage/LineageRightPanel";
import { LineageToolbar } from "@/features/lineage/LineageToolbar";
import { LineageTopBar } from "@/features/lineage/LineageTopBar";
import {
  buildLineageGraphModel,
  datasetNodeId,
  runTypeFromRunId,
  type LineageColorMode,
} from "@/features/lineage/lineage-model";
import type { SelectedRunRef } from "@/features/lineage/lineage-selection";
import { useLineageDatasetExplorer } from "@/features/lineage/use-lineage-dataset-explorer";
import { useLineageGraphSource } from "@/features/lineage/use-lineage-graph";
import { useLineageRunList } from "@/features/lineage/use-lineage-runs";

/** Data Lineage / Data Health — 리니지 그래프와 run/품질 evidence를 한 화면에서 연결한다. */
export default function LineagePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<SelectedRunRef | null>(null);
  const [selectedBranch, setSelectedBranch] = useState("main");
  const [colorMode, setColorMode] = useState<LineageColorMode>("resource");
  const [isLegendVisible, setIsLegendVisible] = useState(true);
  const [rightTab, setRightTab] = useState<RightPanelTab>("detail");
  const [isRightPanelOpen, setIsRightPanelOpen] = useState(true);
  const [bottomTab, setBottomTab] = useState<BottomDockTab>("history");
  const [isBottomOpen, setIsBottomOpen] = useState(true);
  const [layoutSeed, setLayoutSeed] = useState(0);

  const graphSource = useLineageGraphSource();
  const runList = useLineageRunList(100);

  const model = useMemo(
    () =>
      graphSource.data
        ? buildLineageGraphModel(graphSource.data, runList.runRows)
        : null,
    [graphSource.data, runList.runRows],
  );
  const requestedDatasetRef = searchParams.get("ref");

  const selectedNode =
    selectedNodeId && model
      ? (model.nodesById.get(selectedNodeId) ?? null)
      : null;
  const selectedDatasetRef =
    selectedNode?.kind === "dataset" ? selectedNode.datasetRef : null;

  const explorer = useLineageDatasetExplorer({
    namespace: selectedNode?.namespace ?? null,
    name:
      selectedNode?.name && selectedNode.kind === "dataset"
        ? selectedNode.name
        : null,
    previewLimit: 50,
  });

  const branches = useMemo(() => {
    const branchSet = new Set<string>(["main"]);
    for (const versions of Object.values(
      graphSource.data?.versionsByDatasetRef ?? {},
    )) {
      for (const version of versions) branchSet.add(version.branch);
    }
    return [...branchSet];
  }, [graphSource.data]);

  const handleSelectNode = (nodeId: string | null) => {
    setSelectedNodeId(nodeId);
    if (nodeId && rightTab === "search") setRightTab("detail");
    const nextParams = new URLSearchParams(searchParams);
    if (!nodeId) {
      nextParams.delete("ref");
      setSearchParams(nextParams, { replace: true });
      return;
    }
    const nextNode = model?.nodesById.get(nodeId);
    if (nextNode?.kind === "dataset" && nextNode.datasetRef) {
      nextParams.set("ref", nextNode.datasetRef);
      setSearchParams(nextParams, { replace: true });
    }
  };

  const handleSelectDatasetRef = (ref: string) => {
    handleSelectNode(datasetNodeId(ref));
  };

  useEffect(() => {
    if (!requestedDatasetRef || !model) return;
    const requestedNodeId = datasetNodeId(requestedDatasetRef);
    if (!model.nodesById.has(requestedNodeId)) return;
    setSelectedNodeId((previous) =>
      previous === requestedNodeId ? previous : requestedNodeId,
    );
  }, [model, requestedDatasetRef]);

  const handleSelectRunFromGraph = (runId: string | null) => {
    if (!runId) return;
    setSelectedRun({ runType: runTypeFromRunId(runId), runId });
    setRightTab("runs");
    setIsRightPanelOpen(true);
  };

  const handleSelectRun = (run: SelectedRunRef | null) => {
    setSelectedRun(run);
    if (run) {
      setRightTab("runs");
      setIsRightPanelOpen(true);
    }
  };

  const handleMutated = () => {
    void runList.reload();
    void graphSource.reload();
    void explorer.reload();
  };

  const renderCanvasArea = () => {
    if (graphSource.isLoading) {
      return (
        <div className="flex-1 bg-canvas p-6">
          <LoadingState rowCount={8} />
        </div>
      );
    }
    if (graphSource.error) {
      return (
        <div className="flex-1 bg-canvas p-6">
          <ErrorState
            error={graphSource.error}
            onRetry={() => void graphSource.reload()}
          />
        </div>
      );
    }
    if (!model || model.nodes.length === 0) {
      return (
        <div className="flex flex-1 items-center justify-center bg-canvas p-6">
          <EmptyState
            icon={Waypoints}
            title="리니지에 표시할 리소스가 없습니다"
            description="데이터 연결에서 소스를 등록하고 첫 동기화를 실행하면 그래프가 그려집니다."
            action={
              <Button size="sm" asChild>
                <Link to="/data/connections">
                  <Cable className="size-3.5" />
                  데이터 연결로 이동
                </Link>
              </Button>
            }
          />
        </div>
      );
    }
    return (
      <div className="min-w-0 flex-1">
        <LineageCanvas
          model={model}
          colorMode={colorMode}
          isLegendVisible={isLegendVisible}
          selectedNodeId={selectedNodeId}
          layoutSeed={layoutSeed}
          onSelectNode={handleSelectNode}
          onSelectRun={handleSelectRunFromGraph}
        />
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col">
      <LineageTopBar
        datasets={graphSource.data?.datasets ?? []}
        branches={branches}
        selectedDatasetRef={selectedDatasetRef}
        selectedBranch={selectedBranch}
        onSelectDatasetRef={handleSelectDatasetRef}
        onSelectBranch={setSelectedBranch}
      />

      <div className="flex min-h-0 flex-1">
        <LineageToolbar
          colorMode={colorMode}
          isLegendVisible={isLegendVisible}
          onChangeColorMode={setColorMode}
          onToggleLegend={() => setIsLegendVisible((previous) => !previous)}
          onRelayout={() => setLayoutSeed((previous) => previous + 1)}
          onClearSelection={() => handleSelectNode(null)}
          onOpenSearch={() => {
            setRightTab("search");
            setIsRightPanelOpen(true);
          }}
        />
        {renderCanvasArea()}
        <LineageRightPanel
          model={model}
          selectedNode={selectedNode}
          selectedRun={selectedRun}
          activeTab={rightTab}
          isPanelOpen={isRightPanelOpen}
          explorer={explorer}
          runList={runList}
          onChangeTab={setRightTab}
          onTogglePanel={() => setIsRightPanelOpen((previous) => !previous)}
          onSelectNode={handleSelectNode}
          onSelectRun={handleSelectRun}
          onMutated={handleMutated}
        />
      </div>

      <LineageBottomDock
        activeTab={bottomTab}
        isOpen={isBottomOpen}
        explorer={explorer}
        runList={runList}
        datasets={graphSource.data?.datasets ?? []}
        selectedRun={selectedRun}
        onChangeTab={setBottomTab}
        onToggleOpen={() => setIsBottomOpen((previous) => !previous)}
        onSelectRun={handleSelectRun}
      />
    </div>
  );
}
