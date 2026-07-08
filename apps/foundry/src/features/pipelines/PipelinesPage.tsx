import type { PipelineRun } from "@foundry-lite/sdk";
import { GitBranch } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Button } from "@/components/ui/button";

import { BottomDock } from "./components/BottomDock";
import { BranchCreateDialog } from "./components/BranchCreateDialog";
import { BuilderToolbar, type BuilderView } from "./components/BuilderToolbar";
import { CanvasToolbar } from "./components/CanvasToolbar";
import { NodeInspector } from "./components/NodeInspector";
import {
  PipelineFlowCanvas,
  type CanvasFocusSignal,
  type CanvasInteractionMode,
} from "./components/PipelineFlowCanvas";
import { PipelineOutputsTab } from "./components/PipelineOutputsTab";
import { PreviewPanel } from "./components/PreviewPanel";
import { ProposalsPanel } from "./components/ProposalsPanel";
import { ProposeDialog } from "./components/ProposeDialog";
import { RunsPanel } from "./components/RunsPanel";
import { asText } from "./pipeline-model";
import { usePipelineActions } from "./use-pipeline-actions";
import { usePipelineWorkbench } from "./use-pipeline-workbench";

/** Pipeline Builder: 편집(그래프) / 제안(리뷰+diff) / 히스토리(버전·배포·run) 문서 탭 구조. */
export default function PipelinesPage() {
  const workbench = usePipelineWorkbench();
  const [activeView, setActiveView] = useState<BuilderView>("edit");
  const [interactionMode, setInteractionMode] =
    useState<CanvasInteractionMode>("pan");
  const [isBranchDialogOpen, setIsBranchDialogOpen] = useState(false);
  const [isProposeDialogOpen, setIsProposeDialogOpen] = useState(false);
  const [lastRun, setLastRun] = useState<PipelineRun | null>(null);
  const [focusSignal, setFocusSignal] = useState<CanvasFocusSignal | null>(
    null,
  );

  const actions = usePipelineActions({
    onGraphSaved: (branch) => {
      workbench.handleGraphSaved(branch);
      void workbench.graphQuery.reload();
    },
    onBranchCreated: (branch) => {
      void workbench.builder.reload();
      workbench.handleSelectBranch(branch.id);
    },
    onProposalChanged: () => {
      void workbench.builder.reload();
      setActiveView("proposals");
    },
    onVersionChanged: () => setActiveView("history"),
    onRunChanged: (run) => {
      setLastRun(run);
      setActiveView("history");
    },
  });

  const handleSave = useCallback(() => {
    const graph = workbench.buildGraphForSave();
    const expectedFingerprint = asText(workbench.branch?.graphFingerprint);
    if (!graph || !workbench.branchId || !expectedFingerprint) return;
    void actions.saveGraph.execute({
      branchId: workbench.branchId,
      graph,
      expectedFingerprint,
    });
  }, [actions.saveGraph, workbench]);

  const validNodeCount = useMemo(() => {
    const nodeCount = workbench.doc?.nodes.length ?? 0;
    const errorNodeCount = Object.keys(workbench.nodeIssues).length;
    return Math.max(nodeCount - errorNodeCount, 0);
  }, [workbench.doc, workbench.nodeIssues]);

  const outputNodes = useMemo(
    () =>
      (workbench.doc?.nodes ?? []).filter(
        (node) => node.type === "output_dataset",
      ),
    [workbench.doc],
  );

  const handleFocusOutputNode = useCallback(
    (nodeId: string) => {
      workbench.setSelectedNodeId(nodeId);
      setFocusSignal((prev) => ({ nodeId, seq: (prev?.seq ?? 0) + 1 }));
    },
    [workbench],
  );

  if (workbench.builder.isLoading) {
    return (
      <div className="space-y-3 p-4">
        <LoadingState rowCount={2} />
        <LoadingState rowCount={6} className="opacity-60" />
      </div>
    );
  }
  if (workbench.builder.error) {
    return (
      <div className="p-4">
        <ErrorState
          error={workbench.builder.error}
          onRetry={() => void workbench.builder.reload()}
        />
      </div>
    );
  }

  const proposals = workbench.builder.data?.proposals ?? [];
  const hasBranches = workbench.branches.length > 0;

  return (
    <div className="flex h-full flex-col">
      <BuilderToolbar
        branches={workbench.branches}
        branchId={workbench.branchId}
        branch={workbench.branch}
        validation={workbench.validation}
        validNodeCount={validNodeCount}
        proposalCount={proposals.length}
        activeView={activeView}
        isDirty={workbench.isDirty}
        isSaving={actions.saveGraph.isRunning}
        isProposing={actions.propose.isRunning}
        canPropose={Boolean(workbench.branchId) && !workbench.isDirty}
        canUndo={workbench.canUndo}
        canRedo={workbench.canRedo}
        onUndo={workbench.handleUndo}
        onRedo={workbench.handleRedo}
        onViewChange={setActiveView}
        onSelectBranch={workbench.handleSelectBranch}
        onCreateBranch={() => setIsBranchDialogOpen(true)}
        onSave={handleSave}
        onPropose={() => setIsProposeDialogOpen(true)}
      />

      {actions.saveGraph.error ? (
        <div className="border-b px-3 py-2">
          <ErrorState error={actions.saveGraph.error} onRetry={handleSave} />
        </div>
      ) : null}

      {!hasBranches ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={GitBranch}
            title="파이프라인 브랜치가 없습니다"
            description="브랜치를 만들면 그래프 편집, 검증, 제안 리뷰, 실행까지 이어지는 파이프라인 작업을 시작할 수 있습니다."
            action={
              <Button
                size="sm"
                className="text-[12px]"
                onClick={() => setIsBranchDialogOpen(true)}
              >
                첫 브랜치 만들기
              </Button>
            }
          />
        </div>
      ) : (
        <>
          {activeView === "edit" ? (
            <>
              {workbench.graphQuery.isLoading || !workbench.doc ? (
                <LoadingState rowCount={5} className="p-4" />
              ) : workbench.graphQuery.error ? (
                <div className="p-4">
                  <ErrorState
                    error={workbench.graphQuery.error}
                    onRetry={() => void workbench.graphQuery.reload()}
                  />
                </div>
              ) : (
                <>
                  <CanvasToolbar
                    datasets={workbench.datasetsQuery.data ?? []}
                    isDatasetsLoading={workbench.datasetsQuery.isLoading}
                    hasOutputNode={workbench.doc.nodes.some(
                      (node) => node.type === "output_dataset",
                    )}
                    hasSelection={workbench.selectedNodeIds.length > 0}
                    interactionMode={interactionMode}
                    onChangeInteractionMode={setInteractionMode}
                    onSelectAll={workbench.handleSelectAllNodes}
                    onRemoveSelection={() =>
                      workbench.handleDeleteNodes(workbench.selectedNodeIds)
                    }
                    onAddDataset={(dataset) =>
                      void workbench.handleAddDatasetNode(dataset)
                    }
                    onAddTransform={workbench.handleAddTransformNode}
                    onAutoLayout={workbench.handleAutoLayout}
                  />
                  <div className="flex min-h-0 flex-1">
                    <div className="relative min-w-0 flex-1">
                      {workbench.doc.nodes.length === 0 ? (
                        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
                          <div className="pointer-events-auto">
                            <EmptyState
                              title="빈 그래프입니다"
                              description="좌상단 '데이터셋 추가'로 소스 노드를 놓고, 변환과 출력을 연결하세요."
                              className="bg-card/90"
                            />
                          </div>
                        </div>
                      ) : null}
                      <PipelineFlowCanvas
                        graph={workbench.doc}
                        positions={workbench.positions}
                        selectedNodeIds={workbench.selectedNodeIds}
                        interactionMode={interactionMode}
                        nodeIssues={workbench.nodeIssues}
                        focusSignal={focusSignal}
                        onChangeSelection={workbench.setSelectedNodeIds}
                        onMoveNode={workbench.handleMoveNode}
                        onConnectNodes={workbench.handleConnectNodes}
                        onDeleteNodes={workbench.handleDeleteNodes}
                        onDeleteEdges={workbench.handleDeleteEdges}
                        onInsertTransform={workbench.handleInsertNodeBetween}
                      />
                    </div>
                    {workbench.selectedNode ? (
                      <NodeInspector
                        branchId={workbench.branchId}
                        node={workbench.selectedNode}
                        issues={
                          workbench.nodeIssues[workbench.selectedNode.id] ?? []
                        }
                        isGraphDirty={workbench.isDirty}
                        onUpdateNodeData={workbench.handleUpdateNodeData}
                        onClose={() => workbench.setSelectedNodeId(null)}
                      />
                    ) : null}
                    <PipelineOutputsTab
                      outputNodes={outputNodes}
                      selectedNodeIds={workbench.selectedNodeIds}
                      onFocusNode={handleFocusOutputNode}
                    />
                  </div>
                </>
              )}
              <BottomDock>
                <PreviewPanel node={workbench.selectedNode} />
              </BottomDock>
            </>
          ) : null}

          {activeView === "proposals" ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ProposalsPanel proposals={proposals} actions={actions} />
            </div>
          ) : null}

          {activeView === "history" ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <RunsPanel
                pipelineId={asText(workbench.branch?.pipelineId)}
                actions={actions}
                lastRun={lastRun}
              />
            </div>
          ) : null}
        </>
      )}

      <BranchCreateDialog
        isOpen={isBranchDialogOpen}
        onOpenChange={setIsBranchDialogOpen}
        actions={actions}
        defaultPipelineId={asText(workbench.branch?.pipelineId) ?? undefined}
      />
      <ProposeDialog
        isOpen={isProposeDialogOpen}
        onOpenChange={setIsProposeDialogOpen}
        branchId={workbench.branchId}
        actions={actions}
      />
    </div>
  );
}
