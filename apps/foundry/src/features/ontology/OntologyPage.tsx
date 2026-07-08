import type {
  FoundryLiteApiError,
  OntologyBranchPayload,
  OntologyBranchRebaseResolution,
} from "@foundry-lite/sdk";
import {
  ontologyDraftFromCatalog,
  ontologyDraftToYamlText,
} from "@foundry-lite/sdk/ontology-draft";
import type { FoundryLiteOntologyBranchOperation } from "@foundry-lite/sdk/react";
import {
  useFoundryLiteProvidedOntologyBranch,
  useFoundryLiteProvidedOntologyBranchDiff,
  useFoundryLiteProvidedOntologyBranchMutations,
  useFoundryLiteProvidedOntologyBranches,
  useFoundryLiteProvidedOntologyExplorer,
} from "@foundry-lite/sdk/react";
import { Box } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  OntologyRequiredState,
  isActiveOntologyMissingError,
} from "@/components/shared/OntologyRequiredState";

import { BranchDiffPanel } from "./components/BranchDiffPanel";
import { BranchPanel } from "./components/BranchPanel";
import { ColumnMappingPanel } from "./components/ColumnMappingPanel";
import { NewObjectTypeDialog } from "./components/NewObjectTypeDialog";
import { ObjectTypeView } from "./components/ObjectTypeView";
import type { OntologyTab } from "./components/OntologyToolbar";
import { OntologyToolbar } from "./components/OntologyToolbar";
import { OverviewSection } from "./components/OverviewSection";
import { ProposalsView } from "./components/ProposalsView";
import { ResourceDetailPanel } from "./components/ResourceDetailPanel";
import { ResourceNav } from "./components/ResourceNav";
import { ResourceTable } from "./components/ResourceTable";
import { DraftPanel } from "./components/DraftPanel";
import type { OntologySection } from "./lib/ontology-view";
import {
  RESOURCE_KIND_LABELS,
  SECTION_TO_KIND,
  buildDiffKeySet,
  buildResourceRows,
  buildSearchMatchCounts,
  ontologyResourceRowKey,
} from "./lib/ontology-view";

/** Ontology Manager: 카탈로그 탐색 + 브랜치/드래프트/제안 거버넌스 플로우. */
export default function OntologyPage() {
  const [tab, setTab] = useState<OntologyTab>("ontology");
  const [section, setSection] = useState<OntologySection>("objectTypes");
  const [search, setSearch] = useState("");
  const [selectedRowKey, setSelectedRowKey] = useState<string | null>(null);
  const [checkedKeys, setCheckedKeys] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [selectedProposalId, setSelectedProposalId] = useState<string | null>(
    null,
  );
  const [draftOverride, setDraftOverride] = useState<string | null>(null);
  const [isNewObjectTypeOpen, setIsNewObjectTypeOpen] = useState(false);

  const explorer = useFoundryLiteProvidedOntologyExplorer();
  const branches = useFoundryLiteProvidedOntologyBranches({ pageSize: 50 });
  const branchDetail = useFoundryLiteProvidedOntologyBranch(selectedBranchId);
  const branchDiff = useFoundryLiteProvidedOntologyBranchDiff(selectedBranchId);

  const handleBranchMutationSuccess = useCallback(
    (
      branch: OntologyBranchPayload,
      operation: FoundryLiteOntologyBranchOperation,
    ) => {
      if (operation === "create") {
        toast.success(`브랜치 "${branch.name}"를 만들었습니다`);
        setSelectedBranchId(branch.id);
        setDraftOverride(null);
      }
      if (operation === "update") {
        toast.success("드래프트를 브랜치에 저장했습니다");
        setDraftOverride(null);
      }
      if (operation === "rebase") {
        toast.success("브랜치를 메인 기준으로 리베이스했습니다");
        setDraftOverride(null);
      }
      if (operation === "propose") {
        toast.success("변경 제안을 만들었습니다 — 제안 탭에서 리뷰하세요");
        if (branch.proposalId) {
          setSelectedProposalId(branch.proposalId);
          setTab("proposals");
        }
      }
      if (operation === "abandon") {
        toast.success(`브랜치 "${branch.name}"를 폐기했습니다`);
        setSelectedBranchId(null);
        setDraftOverride(null);
      }
      void branches.reload();
      if (operation !== "abandon" && operation !== "create") {
        void branchDetail.refetch();
        void branchDiff.refetch();
      }
    },
    [branches, branchDetail, branchDiff],
  );

  const handleBranchMutationError = useCallback(
    (error: FoundryLiteApiError) => {
      toast.error(`브랜치 작업 실패: ${error.code}`);
    },
    [],
  );

  const branchMutations = useFoundryLiteProvidedOntologyBranchMutations({
    onSuccess: handleBranchMutationSuccess,
    onError: handleBranchMutationError,
  });

  const catalogYaml = useMemo(
    () =>
      explorer.catalog
        ? ontologyDraftToYamlText(ontologyDraftFromCatalog(explorer.catalog))
        : "",
    [explorer.catalog],
  );
  const baseYaml = selectedBranchId
    ? (branchDetail.yamlText ?? "")
    : catalogYaml;
  const yamlText = draftOverride ?? baseYaml;
  const isDraftDirty = draftOverride !== null && draftOverride !== baseYaml;

  const changedKeys = useMemo(
    () => buildDiffKeySet(branchDiff.resources),
    [branchDiff.resources],
  );
  const rows = useMemo(
    () =>
      section === "overview"
        ? []
        : buildResourceRows(section, explorer, changedKeys, search.trim()),
    [section, explorer, changedKeys, search],
  );
  const searchMatchCounts = useMemo(
    () => buildSearchMatchCounts(explorer, changedKeys, search.trim()),
    [explorer, changedKeys, search],
  );
  const selectedRow = useMemo(
    () =>
      rows.find((row) => ontologyResourceRowKey(row) === selectedRowKey) ??
      null,
    [rows, selectedRowKey],
  );
  const selectedObjectView =
    selectedRow?.kind === "objectType"
      ? (explorer.objectViewsByApiName[selectedRow.apiName] ?? null)
      : null;
  const selectedActionView =
    selectedRow?.kind === "actionType"
      ? (explorer.actionViewsByApiName[selectedRow.apiName] ?? null)
      : null;
  /** 객체 타입 행이 선택되면 전체폭 편집 뷰(ObjectTypeView)를 띄운다. */
  const isObjectTypeDetail =
    section === "objectTypes" && selectedRow?.kind === "objectType";
  /** 열린 브랜치가 편집 대상일 때만 폼 편집이 저장으로 이어진다. */
  const isBranchEditable =
    selectedBranchId !== null &&
    branchDetail.branch?.id === selectedBranchId &&
    branchDetail.branch.status === "open";

  const propertyCount = useMemo(
    () =>
      explorer.objectViews.reduce((sum, view) => sum + view.propertyCount, 0),
    [explorer.objectViews],
  );
  const navCounts = {
    objectTypes: explorer.objectCount,
    properties: propertyCount,
    linkTypes: explorer.linkCount,
    actionTypes: explorer.actionCount,
    interfaces: explorer.catalog?.interfaces?.length ?? 0,
    functionTypes: explorer.catalog?.functionTypes?.length ?? 0,
  };
  const versionLabel = explorer.catalog
    ? `v${explorer.catalog.versionNumber} 활성`
    : null;

  const handleSelectSection = (nextSection: OntologySection) => {
    setSection(nextSection);
    setSelectedRowKey(null);
    setCheckedKeys(new Set());
  };
  const handleSelectBranch = (branchId: string | null) => {
    setSelectedBranchId(branchId);
    setDraftOverride(null);
  };
  const handleToggleCheck = (key: string) => {
    setCheckedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const handleToggleAllChecks = (isChecked: boolean) => {
    setCheckedKeys(
      isChecked ? new Set(rows.map(ontologyResourceRowKey)) : new Set(),
    );
  };
  const handleOpenDraftPanel = () => {
    document
      .getElementById("ontology-draft-panel")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
    toast.info("리소스 추가는 YAML 드래프트 편집기에서 진행하세요");
  };
  const handleRequestNewResource = () => {
    if (section === "objectTypes") {
      setIsNewObjectTypeOpen(true);
      return;
    }
    handleOpenDraftPanel();
  };
  const handleRebase = (resolutions: OntologyBranchRebaseResolution[]) => {
    if (!selectedBranchId || !branchDetail.fingerprint) return;
    void branchMutations.rebase.execute({
      branchId: selectedBranchId,
      expectedFingerprint: branchDetail.fingerprint,
      resolutions,
    });
  };
  const handlePropose = (input: {
    title: string;
    description: string | null;
    idempotencyKey: string;
  }) => {
    if (!selectedBranchId) return Promise.resolve(null);
    return branchMutations.propose.execute({
      branchId: selectedBranchId,
      title: input.title,
      description: input.description,
      idempotencyKey: input.idempotencyKey,
    });
  };
  const handleSaveToBranch = (nextYamlText: string) => {
    if (!selectedBranchId || !branchDetail.fingerprint) return;
    void branchMutations.update.execute({
      branchId: selectedBranchId,
      yamlText: nextYamlText,
      expectedFingerprint: branchDetail.fingerprint,
    });
  };
  const handleOntologyChanged = () => {
    explorer.reload();
    if (selectedBranchId) void branchDiff.refetch();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <OntologyToolbar
        tab={tab}
        versionLabel={versionLabel}
        search={search}
        isDraftDirty={isDraftDirty}
        editingBranchName={
          selectedBranchId !== null &&
          branchDetail.branch?.id === selectedBranchId &&
          branchDetail.branch.status === "open"
            ? branchDetail.branch.name
            : null
        }
        isSavingToBranch={branchMutations.update.isRunning}
        createMutation={branchMutations.create}
        onTabChange={setTab}
        onSearchChange={setSearch}
        onDiscardDraft={() => setDraftOverride(null)}
        onReturnToMain={() => handleSelectBranch(null)}
        onSaveToBranch={() => handleSaveToBranch(yamlText)}
        onCreateBranch={(payload) => branchMutations.create.execute(payload)}
      />
      {tab === "proposals" ? (
        <ProposalsView
          selectedProposalId={selectedProposalId}
          onSelectProposal={setSelectedProposalId}
        />
      ) : (
        <div className="flex min-h-0 flex-1 items-start gap-3 overflow-auto bg-canvas p-3">
          {isObjectTypeDetail ? null : (
            <aside className="w-[272px] shrink-0 space-y-3">
              <BranchPanel
                branches={branches}
                branchDetail={branchDetail}
                selectedBranchId={selectedBranchId}
                abandonMutation={branchMutations.abandon}
                onSelectBranch={handleSelectBranch}
                onAbandonBranch={(branchId) =>
                  void branchMutations.abandon.execute({ branchId })
                }
              />
              <ResourceNav
                section={section}
                counts={navCounts}
                matchCounts={searchMatchCounts}
                onSelectSection={handleSelectSection}
              />
            </aside>
          )}

          {isObjectTypeDetail && explorer.catalog && selectedRow ? (
            <ObjectTypeView
              apiName={selectedRow.apiName}
              objectView={selectedObjectView}
              catalog={explorer.catalog}
              versionLabel={versionLabel}
              yamlText={yamlText}
              isEditable={isBranchEditable}
              isDraftDirty={isDraftDirty}
              isIndexedOnBranch={selectedBranchId !== null}
              branchDetail={branchDetail}
              updateMutation={branchMutations.update}
              onDraftChange={setDraftOverride}
              onSaveToBranch={handleSaveToBranch}
              onOntologyChanged={handleOntologyChanged}
              onBack={() => setSelectedRowKey(null)}
            />
          ) : (
            <main className="min-w-0 flex-1 space-y-3">
              {explorer.isLoading ? (
                <LoadingState rowCount={8} />
              ) : explorer.error &&
                isActiveOntologyMissingError(explorer.error) ? (
                <OntologyRequiredState />
              ) : explorer.error ? (
                <ErrorState error={explorer.error} onRetry={explorer.reload} />
              ) : !explorer.catalog ? (
                <EmptyState
                  icon={Box}
                  title="활성 온톨로지가 없습니다"
                  description="데이터셋 스키마를 기반으로 드래프트를 작성하고 적용해 첫 온톨로지 버전을 만드세요."
                />
              ) : section === "overview" ? (
                <OverviewSection
                  catalog={explorer.catalog}
                  propertyCount={propertyCount}
                />
              ) : (
                <div className="flex items-start gap-3">
                  <ResourceTable
                    className="min-w-0 flex-1"
                    sectionLabel={
                      RESOURCE_KIND_LABELS[SECTION_TO_KIND[section]]
                    }
                    rows={rows}
                    search={search.trim()}
                    isLoading={false}
                    error={null}
                    selectedKey={selectedRowKey}
                    checkedKeys={checkedKeys}
                    onRetry={explorer.reload}
                    onSelectRow={(row) =>
                      setSelectedRowKey((current) => {
                        const key = ontologyResourceRowKey(row);
                        return current === key ? null : key;
                      })
                    }
                    onToggleCheck={handleToggleCheck}
                    onToggleAllChecks={handleToggleAllChecks}
                    onRequestNew={handleRequestNewResource}
                  />
                  {selectedRow ? (
                    <ResourceDetailPanel
                      row={selectedRow}
                      objectView={selectedObjectView}
                      actionView={selectedActionView}
                      catalog={explorer.catalog}
                      versionLabel={versionLabel}
                      onClose={() => setSelectedRowKey(null)}
                    />
                  ) : null}
                </div>
              )}

              <ColumnMappingPanel catalog={explorer.catalog} />
              <BranchDiffPanel
                branchDetail={branchDetail}
                diff={branchDiff}
                rebaseMutation={branchMutations.rebase}
                proposeMutation={branchMutations.propose}
                onRebase={handleRebase}
                onPropose={handlePropose}
              />
              <DraftPanel
                yamlText={yamlText}
                isDraftDirty={isDraftDirty}
                branchDetail={branchDetail}
                updateMutation={branchMutations.update}
                activeVersionNumber={explorer.catalog?.versionNumber ?? null}
                onYamlChange={setDraftOverride}
                onSaveToBranch={handleSaveToBranch}
                onOntologyChanged={handleOntologyChanged}
              />
            </main>
          )}
          <NewObjectTypeDialog
            open={isNewObjectTypeOpen}
            onOpenChange={setIsNewObjectTypeOpen}
            catalog={explorer.catalog}
            yamlText={yamlText}
            canSaveToBranch={
              branchDetail.branch !== null &&
              branchDetail.branch.status === "open"
            }
            branchName={branchDetail.branch?.name ?? null}
            updateMutation={branchMutations.update}
            onDraftGenerated={setDraftOverride}
            onSaveToBranch={handleSaveToBranch}
          />
        </div>
      )}
    </div>
  );
}
