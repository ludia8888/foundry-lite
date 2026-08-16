import type { PipelineRun } from "@foundry-lite/sdk";
import { GitBranch } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { BottomDock } from "./components/BottomDock";
import { BranchCreateDialog } from "./components/BranchCreateDialog";
import { BuilderToolbar, type BuilderView } from "./components/BuilderToolbar";
import { CanvasToolbar } from "./components/CanvasToolbar";
import { NodeInspector } from "./components/NodeInspector";
import { PipelineCapabilityCatalog } from "./components/PipelineCapabilityCatalog";
import { PipelineNodeConfigurationBoard } from "./components/PipelineNodeConfigurationBoard";
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
import {
  asText,
  importedTrainedModelRefs,
  isDedicatedConfigurationNode,
  isOutputNode,
  nodeLabel,
  trainedModelUsageNodeIds,
} from "./pipeline-model";
import { usePipelineActions } from "./use-pipeline-actions";
import { usePipelineWorkbench } from "./use-pipeline-workbench";

/** Pipeline Builder: 편집(그래프) / 제안(리뷰+diff) / 히스토리(버전·배포·run) 문서 탭 구조. */
export default function PipelinesPage() {
  const workbench = usePipelineWorkbench();
  const [activeView, setActiveView] = useState<BuilderView>("edit");
  const [interactionMode, setInteractionMode] =
    useState<CanvasInteractionMode>("pan");
  const [isCatalogOpen, setIsCatalogOpen] = useState(false);
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

  const handleRebase = useCallback(() => {
    const expectedFingerprint = asText(workbench.branch?.graphFingerprint);
    if (!workbench.branchId || !expectedFingerprint) return;
    void actions.rebaseBranch.execute({
      branchId: workbench.branchId,
      expectedFingerprint,
    });
  }, [actions.rebaseBranch, workbench.branch?.graphFingerprint, workbench.branchId]);

  const validNodeCount = useMemo(() => {
    const nodeCount = workbench.doc?.nodes.length ?? 0;
    const errorNodeCount = Object.keys(workbench.nodeIssues).length;
    return Math.max(nodeCount - errorNodeCount, 0);
  }, [workbench.doc, workbench.nodeIssues]);
  const testedFingerprint = asText(
    actions.runBranchTests.result?.graphFingerprint,
  );
  const testedBranchId = asText(actions.runBranchTests.result?.branchId);
  const currentFingerprint = asText(workbench.branch?.graphFingerprint);
  const testState = useMemo(() => {
    const result = actions.runBranchTests.result;
    if (!result) return "missing" as const;
    if (
      testedBranchId !== workbench.branchId ||
      testedFingerprint !== currentFingerprint
    )
      return "stale" as const;
    return result.status === "passed" ? ("passed" as const) : ("failed" as const);
  }, [
    actions.runBranchTests.result,
    currentFingerprint,
    testedBranchId,
    testedFingerprint,
    workbench.branchId,
  ]);
  const canPropose =
    Boolean(workbench.branchId) &&
    !workbench.isDirty &&
    testState === "passed";

  const handleRunTests = useCallback(() => {
    if (!workbench.branchId) return;
    void actions.runBranchTests.execute({ branchId: workbench.branchId });
  }, [actions.runBranchTests, workbench.branchId]);

  const outputNodes = useMemo(
    () =>
      (workbench.doc?.nodes ?? []).filter(
        (node) => isOutputNode(node),
      ),
    [workbench.doc],
  );
  const previewGraph = useMemo(
    () => workbench.buildGraphForSave(),
    [workbench.buildGraphForSave],
  );
  const trainedModelUsageByRef = useMemo(() => {
    const refs = importedTrainedModelRefs(workbench.doc);
    return Object.fromEntries(
      refs.map((modelRef) => [
        modelRef,
        trainedModelUsageNodeIds(workbench.doc, modelRef),
      ]),
    );
  }, [workbench.doc]);

  const handleFocusOutputNode = useCallback(
    (nodeId: string) => {
      workbench.setSelectedNodeId(nodeId);
      setFocusSignal((prev) => ({ nodeId, seq: (prev?.seq ?? 0) + 1 }));
    },
    [workbench],
  );
  const handleViewChange = useCallback((view: BuilderView) => {
    setIsCatalogOpen(false);
    setActiveView(view);
  }, []);

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
    <div className="pipeline-builder-workspace flex h-full flex-col overflow-hidden">
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
        isTesting={actions.runBranchTests.isRunning}
        testState={testState}
        canPropose={canPropose}
        canUndo={workbench.canUndo}
        canRedo={workbench.canRedo}
        isBaseStale={workbench.diff?.baseStale === true}
        isProtected={workbench.branch?.protection.requiresProposal === true}
        isRebasing={actions.rebaseBranch.isRunning}
        onUndo={workbench.handleUndo}
        onRedo={workbench.handleRedo}
        onViewChange={handleViewChange}
        onSelectBranch={workbench.handleSelectBranch}
        onCreateBranch={() => setIsBranchDialogOpen(true)}
        onRebase={handleRebase}
        onSave={handleSave}
        onRunTests={handleRunTests}
        onPropose={() => setIsProposeDialogOpen(true)}
      />

      {actions.runBranchTests.error ? (
        <div className="border-b px-3 py-2">
          <ErrorState
            error={actions.runBranchTests.error}
            onRetry={handleRunTests}
          />
        </div>
      ) : null}
      {actions.runBranchTests.result ? (
        <div
          aria-label="작업 테스트 결과"
          aria-live="polite"
          className={cn(
            "border-b px-3 py-1.5 text-[11px]",
            testState === "passed"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : testState === "failed"
                ? "border-red-200 bg-red-50 text-red-800"
                : "border-amber-200 bg-amber-50 text-amber-900",
          )}
          role="status"
        >
          {testState === "passed"
            ? `제안 준비 완료 · 저장된 작업 테스트 ${String(actions.runBranchTests.result.testCount ?? 0)}개 통과`
            : testState === "failed"
              ? `제안 전 수정 필요 · 실패 ${String((actions.runBranchTests.result.failures as unknown[] | undefined)?.length ?? 0)}건`
              : "저장된 작업이 바뀌었습니다 · 다시 테스트하면 제안할 수 있습니다"}
        </div>
      ) : null}

      {actions.saveGraph.error ? (
        <div className="border-b px-3 py-2">
          <ErrorState error={actions.saveGraph.error} onRetry={handleSave} />
        </div>
      ) : null}
      {actions.rebaseBranch.error ? (
        <div className="border-b px-3 py-2">
          <ErrorState
            error={actions.rebaseBranch.error}
            onRetry={handleRebase}
          />
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
              ) : isCatalogOpen ? (
                <PipelineCapabilityCatalog
                  hasOutputNode={workbench.doc.nodes.some(
                    (node) => isOutputNode(node),
                  )}
                  contextLabel={
                    workbench.selectedNode
                      ? nodeLabel(workbench.selectedNode)
                      : "파이프라인 그래프"
                  }
                  importedTrainedModelRefs={importedTrainedModelRefs(
                    workbench.doc,
                  )}
                  trainedModelUsageByRef={trainedModelUsageByRef}
                  onImportTrainedModel={workbench.handleImportTrainedModel}
                  onRemoveTrainedModel={workbench.handleRemoveTrainedModel}
                  onAddDescriptor={workbench.handleAddDescriptorNode}
                  onClose={() => setIsCatalogOpen(false)}
                />
              ) : isDedicatedConfigurationNode(workbench.selectedNode) &&
                workbench.selectedNode ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  <PipelineNodeConfigurationBoard
                    key={workbench.selectedNode.id}
                    node={workbench.selectedNode}
                    branchId={workbench.branchId}
                    graph={previewGraph}
                    isGraphDirty={workbench.isDirty}
                    onApply={workbench.handleUpdateNodeData}
                    onClose={() => workbench.setSelectedNodeId(null)}
                  />
                  {![
                    "transform.use_llm",
                    "transform.trained_model",
                  ].includes(workbench.selectedNode.descriptorId) ? (
                    <BottomDock>
                      <PreviewPanel
                        branchId={workbench.branchId}
                        graph={previewGraph}
                        node={workbench.selectedNode}
                        isGraphDirty={workbench.isDirty}
                      />
                    </BottomDock>
                  ) : null}
                </div>
              ) : (
                <>
                  <CanvasToolbar
                    datasets={workbench.datasetsQuery.data ?? []}
                    isDatasetsLoading={workbench.datasetsQuery.isLoading}
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
                    onOpenCatalog={() => setIsCatalogOpen(true)}
                  />
                  <div className="flex min-h-0 flex-1">
                    <div className="relative min-w-0 flex-1">
                      {workbench.connectionIssue ? (
                        <div className="absolute top-2 left-1/2 z-20 max-w-xl -translate-x-1/2 border border-[#D9822B] bg-[#FFF4E8] px-3 py-2 text-[11px] text-[#7A4314] shadow-sm">
                          {workbench.connectionIssue}
                        </div>
                      ) : null}
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
                        node={workbench.selectedNode}
                        issues={
                          workbench.nodeIssues[workbench.selectedNode.id] ?? []
                        }
                        onUpdateNodeData={workbench.handleUpdateNodeData}
                        onClose={() => workbench.setSelectedNodeId(null)}
                      />
                    ) : null}
                    <PipelineOutputsTab
                      outputNodes={outputNodes}
                      selectedNodeIds={workbench.selectedNodeIds}
                      onFocusNode={handleFocusOutputNode}
                      onAddOutput={() => setIsCatalogOpen(true)}
                    />
                  </div>
                  <BottomDock>
                    <PreviewPanel
                      branchId={workbench.branchId}
                      graph={previewGraph}
                      node={workbench.selectedNode}
                      isGraphDirty={workbench.isDirty}
                    />
                  </BottomDock>
                </>
              )}
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
