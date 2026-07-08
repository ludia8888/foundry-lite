import {
  useFoundryLiteProvidedObjectActionWorkspace,
  type FoundryLiteOntologyActionView,
} from "@foundry-lite/sdk/react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  OntologyRequiredState,
  isActiveOntologyMissingError,
} from "@/components/shared/OntologyRequiredState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { StatusPill } from "@/components/shared/StatusPill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ActionFormPanel } from "./components/ActionFormPanel";
import { ActionTypeList } from "./components/ActionTypeList";
import { BatchApplyPanel } from "./components/BatchApplyPanel";
import { FunctionsPanel } from "./components/FunctionsPanel";
import { ObjectPicker } from "./components/ObjectPicker";
import { ProposalBridgePanel } from "./components/ProposalBridgePanel";
import { SubmissionCriteriaPanel } from "./components/SubmissionCriteriaPanel";
import { actionDefinitionView } from "./lib/action-definition";

type WorkspaceTab = "form" | "batch" | "functions";

/** Action Types / Functions: 액션 폼 런타임 · 배치 실행 · 함수 실행 워크스페이스. */
export default function ActionsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<WorkspaceTab>("form");
  const [selectedActionApiName, setSelectedActionApiName] = useState<
    string | null
  >(null);
  const [selectedObjectApiName, setSelectedObjectApiName] = useState<
    string | null
  >(null);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [dataVersion, setDataVersion] = useState(0);

  const workspace = useFoundryLiteProvidedObjectActionWorkspace({
    selectedActionApiName,
    // 액션 target 객체 타입을 객체 쿼리 대상으로 고정해 대상 후보를 로드한다.
    selectedObjectApiName,
    selectedObjectId,
    key: ["actions-workspace", "catalog", dataVersion],
    objectQuery: {
      pageSize: 50,
      key: ["actions-workspace", "objects", dataVersion],
    },
  });

  const selectedActionView = workspace.explorer.selectedActionView;
  const definition = useMemo(
    () => actionDefinitionView(selectedActionView),
    [selectedActionView],
  );

  const handleSelectAction = (actionView: FoundryLiteOntologyActionView) => {
    setSelectedActionApiName(actionView.apiName);
    setSelectedObjectApiName(actionView.targetObjectApiName);
    setSelectedObjectId(null);
  };

  const handleApplied = () => setDataVersion((version) => version + 1);

  if (workspace.isLoading && !workspace.catalog) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="Action Types / Functions"
          description="액션 폼 실행과 함수 호출"
        />
        <LoadingState rowCount={6} />
      </div>
    );
  }

  if (
    workspace.error &&
    !workspace.catalog &&
    isActiveOntologyMissingError(workspace.error)
  ) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="Action Types / Functions"
          description="액션 폼 실행과 함수 호출"
        />
        <OntologyRequiredState />
      </div>
    );
  }

  if (workspace.error && !workspace.catalog) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="Action Types / Functions"
          description="액션 폼 실행과 함수 호출"
        />
        <ErrorState
          error={workspace.error}
          onRetry={() => workspace.reload()}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b bg-card px-4 py-3">
        <PageHeader
          title="Action Types / Functions"
          description="객체에 검증 가능한 액션을 폼·배치로 실행하고 함수를 호출합니다."
          meta={
            workspace.versionLabel ? (
              <StatusPill intent="neutral">{workspace.versionLabel}</StatusPill>
            ) : null
          }
          actions={<RequestTelemetry />}
        />
      </div>

      <Tabs
        value={tab}
        onValueChange={(value) => setTab(value as WorkspaceTab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="border-b bg-card px-4">
          <TabsList variant="line" className="h-auto flex-wrap gap-x-4">
            <TabsTrigger value="form">폼 실행</TabsTrigger>
            <TabsTrigger value="batch">배치 실행</TabsTrigger>
            <TabsTrigger value="functions">Functions</TabsTrigger>
          </TabsList>
        </div>

        <div className="min-h-0 flex-1 overflow-auto bg-canvas p-4">
          <TabsContent value="form" className="mt-0">
            <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[240px_minmax(0,1fr)_300px]">
              <ActionTypeList
                actionViews={workspace.actionViews}
                selectedApiName={selectedActionView?.apiName ?? null}
                onSelect={handleSelectAction}
              />

              <div className="space-y-3">
                {selectedActionView ? (
                  <>
                    <div className="space-y-0.5">
                      <div className="text-sm font-medium">
                        {selectedActionView.displayName}
                      </div>
                      <div className="font-mono text-[10px] text-muted-foreground">
                        {selectedActionView.apiName} · 파라미터{" "}
                        {selectedActionView.parameterCount}개
                      </div>
                    </div>
                    <ObjectPicker
                      objects={workspace.objects}
                      targetObjectApiName={
                        selectedActionView.targetObjectApiName
                      }
                      selectedObjectId={selectedObjectId}
                      onSelect={setSelectedObjectId}
                      statusProperty="status"
                    />
                    <div className="rounded border bg-card p-3">
                      {workspace.selectedObject ? (
                        <ActionFormPanel
                          actionView={selectedActionView}
                          targetObject={workspace.selectedObject}
                          onApplied={handleApplied}
                        />
                      ) : (
                        <p className="text-[11px] text-muted-foreground">
                          위에서 대상 객체를 선택하면 파라미터 폼이 열립니다.
                        </p>
                      )}
                    </div>
                  </>
                ) : (
                  <EmptyState
                    title="액션 타입을 선택하세요"
                    description="좌측 목록에서 실행할 액션을 고르면 대상 객체와 파라미터 폼이 열립니다."
                  />
                )}
              </div>

              <div className="space-y-3">
                {selectedActionView ? (
                  <SubmissionCriteriaPanel definition={definition} />
                ) : null}
                <ProposalBridgePanel
                  onOpenApprovals={() => navigate("/approvals")}
                />
              </div>
            </div>
          </TabsContent>

          <TabsContent value="batch" className="mt-0">
            <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
              <ActionTypeList
                actionViews={workspace.actionViews}
                selectedApiName={selectedActionView?.apiName ?? null}
                onSelect={handleSelectAction}
              />
              <div className="rounded border bg-card p-3">
                {selectedActionView ? (
                  <BatchApplyPanel
                    actionView={selectedActionView}
                    definition={definition}
                    objects={workspace.objects}
                    onApplied={handleApplied}
                  />
                ) : (
                  <EmptyState
                    title="액션 타입을 선택하세요"
                    description="배치로 실행할 액션을 좌측에서 고르세요."
                  />
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="functions" className="mt-0">
            <FunctionsPanel catalog={workspace.catalog} />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
