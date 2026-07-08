import { useFoundryLiteProvidedObjectActionWorkspace } from "@foundry-lite/sdk/react";
import { useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  OntologyRequiredState,
  isActiveOntologyMissingError,
} from "@/components/shared/OntologyRequiredState";

import { ActionBar, type ExploreViewTab } from "./components/ActionBar";
import { ActionFormDialog } from "./components/ActionFormDialog";
import { AggregatePanel } from "./components/AggregatePanel";
import { ExplorationTabBar } from "./components/ExplorationTabBar";
import { FilterChipBar } from "./components/FilterChipBar";
import { ObjectDetailView } from "./components/ObjectDetailView";
import { ObjectSetDialog } from "./components/ObjectSetDialog";
import { ResultsRail, type SortSpec } from "./components/ResultsRail";
import { ResultsTable } from "./components/ResultsTable";
import { useLiveObjects } from "./hooks/use-live-objects";
import type { FoundryLiteOntologyActionView } from "@foundry-lite/sdk/react";
import { downloadObjectsCsv } from "./lib/export-csv";
import {
  buildObjectFilter,
  createExploration,
  type Exploration,
  type ExplorationSnapshot,
  type FilterPill,
  type LinkOrigin,
  type ObjectRef,
} from "./lib/explorer-model";

const PAGE_SIZE = 50;

/** 필터/검색 변경 1건을 히스토리에 쌓아 undo/redo 가능한 새 탐색 상태를 만든다. */
function withSnapshot(
  exploration: Exploration,
  snapshot: ExplorationSnapshot,
): Exploration {
  const history = [
    ...exploration.history.slice(0, exploration.historyIndex + 1),
    snapshot,
  ];
  return {
    ...exploration,
    pills: snapshot.pills,
    search: snapshot.search,
    history,
    historyIndex: history.length - 1,
  };
}

function atHistoryIndex(exploration: Exploration, index: number): Exploration {
  const snapshot = exploration.history[index];
  if (!snapshot) return exploration;
  return {
    ...exploration,
    pills: snapshot.pills,
    search: snapshot.search,
    historyIndex: index,
  };
}

/** Object Explorer / ObjectSet: 객체 탐색·필터·집계 + linked object + 액션 실행. */
export default function ObjectExplorerPage() {
  const [explorations, setExplorations] = useState<Exploration[]>([]);
  const [activeExplorationId, setActiveExplorationId] = useState<string | null>(
    null,
  );
  const [viewTab, setViewTab] = useState<ExploreViewTab>("explore");
  const [sortByExplorationId, setSortByExplorationId] = useState<
    Record<string, SortSpec | null>
  >({});
  const [pageCursor, setPageCursor] = useState<string | null>(null);
  const [detailStack, setDetailStack] = useState<ObjectRef[]>([]);
  const [isObjectSetOpen, setIsObjectSetOpen] = useState(false);
  const [pendingAction, setPendingAction] =
    useState<FoundryLiteOntologyActionView | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);

  const activeExploration =
    explorations.find(
      (exploration) => exploration.id === activeExplorationId,
    ) ?? null;
  const activeTypeName = activeExploration?.typeName ?? null;
  const pills = activeExploration?.pills ?? [];
  const appliedSearch = activeExploration?.search ?? "";
  const sort = activeExplorationId
    ? (sortByExplorationId[activeExplorationId] ?? null)
    : null;

  const where = useMemo(() => buildObjectFilter(pills), [pills]);
  const orderBy = useMemo(
    () =>
      sort
        ? [{ property: sort.property, direction: sort.direction }]
        : undefined,
    [sort],
  );
  const activeDetail = detailStack[detailStack.length - 1] ?? null;

  const workspace = useFoundryLiteProvidedObjectActionWorkspace({
    selectedObjectApiName: activeTypeName,
    selectedObjectId: activeDetail?.objectId ?? null,
    objectQuery: {
      where,
      orderBy,
      pageSize: PAGE_SIZE,
      pageToken: pageCursor,
      search: appliedSearch || null,
    },
  });
  const activeView = activeTypeName
    ? (workspace.objectViewsByApiName[activeTypeName] ?? null)
    : null;
  const objectQuery = workspace.objectQuery;

  useEffect(() => {
    if (explorations.length > 0 || workspace.objectViews.length === 0) return;
    const first = createExploration(workspace.objectViews[0].apiName);
    setExplorations([first]);
    setActiveExplorationId(first.id);
  }, [explorations.length, workspace.objectViews]);

  const handleQueryChanged = () => {
    setPageCursor(null);
    setDetailStack([]);
  };

  const handleSelectExploration = (explorationId: string) => {
    if (explorationId === activeExplorationId) return;
    setActiveExplorationId(explorationId);
    setIsLive(false);
    handleQueryChanged();
  };

  const handleOpenExploration = (
    typeName: string,
    linkOrigin: LinkOrigin | null = null,
  ) => {
    const next = createExploration(typeName, linkOrigin);
    setExplorations((previous) => [...previous, next]);
    setActiveExplorationId(next.id);
    setIsLive(false);
    handleQueryChanged();
  };

  const handleCloseExploration = (explorationId: string) => {
    const remaining = explorations.filter(
      (exploration) => exploration.id !== explorationId,
    );
    setExplorations(remaining);
    if (explorationId === activeExplorationId) {
      setActiveExplorationId(remaining[0]?.id ?? null);
      setIsLive(false);
      handleQueryChanged();
    }
  };

  const updateActiveExploration = (
    updater: (exploration: Exploration) => Exploration,
  ) => {
    if (!activeExplorationId) return;
    setExplorations((previous) =>
      previous.map((exploration) =>
        exploration.id === activeExplorationId
          ? updater(exploration)
          : exploration,
      ),
    );
    handleQueryChanged();
  };

  const applySnapshot = (nextPills: FilterPill[], nextSearch: string) => {
    updateActiveExploration((exploration) =>
      withSnapshot(exploration, { pills: nextPills, search: nextSearch }),
    );
  };

  const handleUndo = () => {
    updateActiveExploration((exploration) =>
      atHistoryIndex(exploration, exploration.historyIndex - 1),
    );
  };
  const handleRedo = () => {
    updateActiveExploration((exploration) =>
      atHistoryIndex(exploration, exploration.historyIndex + 1),
    );
  };

  const handleObjectsChanged = () => {
    void workspace.reloadObjects();
    setDataVersion((previous) => previous + 1);
  };

  const live = useLiveObjects(
    activeTypeName,
    where,
    isLive,
    handleObjectsChanged,
  );
  const liveSummary = isLive
    ? `이벤트 ${live.eventCount} · 변경 ${live.changeCount}${
        live.watermark !== null ? ` · wm=${live.watermark}` : ""
      }`
    : null;

  if (workspace.isLoading && !workspace.catalog) {
    return (
      <div className="p-4">
        <LoadingState rowCount={8} />
      </div>
    );
  }
  if (
    workspace.error &&
    !workspace.catalog &&
    isActiveOntologyMissingError(workspace.error)
  ) {
    return (
      <div className="p-4">
        <OntologyRequiredState />
      </div>
    );
  }
  if (workspace.error && !workspace.catalog) {
    return (
      <div className="p-4">
        <ErrorState
          error={workspace.error}
          onRetry={() => void workspace.reload()}
        />
      </div>
    );
  }
  if (workspace.catalog && workspace.objectViews.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          title="온톨로지에 객체 타입이 없습니다"
          description="온톨로지 매니저에서 객체 타입을 정의하면 이 화면에서 탐색할 수 있습니다."
        />
      </div>
    );
  }

  const explorationName = activeView ? `${activeView.displayName} 탐색` : "";
  const originView = activeExploration?.linkOrigin
    ? (workspace.objectViewsByApiName[
        activeExploration.linkOrigin.fromTypeName
      ] ?? null)
    : null;
  const actionTarget = objectQuery.items[0] ?? null;

  return (
    <div className="flex h-full min-h-0 min-w-[980px] flex-col">
      <ExplorationTabBar
        objectViews={workspace.objectViews}
        explorations={explorations}
        activeExplorationId={activeExplorationId}
        onSelectExploration={handleSelectExploration}
        onOpenExploration={(typeName) => handleOpenExploration(typeName)}
        onCloseExploration={handleCloseExploration}
        onOpenObjectSets={() => setIsObjectSetOpen(true)}
      />
      <FilterChipBar
        objectView={activeView}
        explorationName={explorationName}
        linkOrigin={activeExploration?.linkOrigin ?? null}
        originView={originView}
        pills={pills}
        appliedSearch={appliedSearch}
        onAddPill={(pill) => applySnapshot([...pills, pill], appliedSearch)}
        onRemovePill={(pillId) =>
          applySnapshot(
            pills.filter((pill) => pill.id !== pillId),
            appliedSearch,
          )
        }
        onClearAll={() => applySnapshot([], "")}
        onApplySearch={(text) => applySnapshot(pills, text)}
        onOpenOrigin={(typeName) => {
          const existing = explorations.find(
            (exploration) => exploration.typeName === typeName,
          );
          if (existing) handleSelectExploration(existing.id);
          else handleOpenExploration(typeName);
        }}
        onOpenSaveObjectSet={() => setIsObjectSetOpen(true)}
      />
      {activeDetail ? (
        <ObjectDetailView
          objectRef={activeDetail}
          objectView={
            workspace.objectViewsByApiName[activeDetail.objectType] ?? null
          }
          canGoBack={detailStack.length > 1}
          onBack={() => setDetailStack((previous) => previous.slice(0, -1))}
          onClose={() => setDetailStack([])}
          onOpenLinked={(ref) =>
            setDetailStack((previous) => [...previous, ref])
          }
          onObjectsChanged={handleObjectsChanged}
        />
      ) : (
        <>
          <ActionBar
            resultCount={objectQuery.items.length}
            viewTab={viewTab}
            onViewTabChange={setViewTab}
            canUndo={(activeExploration?.historyIndex ?? 0) > 0}
            canRedo={
              activeExploration !== null &&
              activeExploration.historyIndex <
                activeExploration.history.length - 1
            }
            onUndo={handleUndo}
            onRedo={handleRedo}
            actions={activeView?.actions ?? []}
            onRunAction={(action) => {
              if (!actionTarget) return;
              setPendingAction(action);
            }}
            onExportCsv={() => {
              if (activeView) downloadObjectsCsv(objectQuery.items, activeView);
            }}
            canExport={activeView !== null && objectQuery.items.length > 0}
            isLive={isLive}
            onLiveChange={setIsLive}
            isStreaming={live.isStreaming}
            liveSummary={liveSummary}
            onRefresh={handleObjectsChanged}
          />
          {live.error ? (
            <ErrorState error={live.error} className="m-3 mb-0" />
          ) : null}
          {!activeView ? (
            <div className="p-4">
              <LoadingState rowCount={6} />
            </div>
          ) : viewTab === "explore" ? (
            <div className="min-h-0 flex-1 overflow-auto bg-[#f6f8fa]">
              <div className="mx-auto flex max-w-[1330px] items-start gap-4 px-4 py-4">
                <div className="min-w-0 flex-1">
                  <AggregatePanel
                    objectView={activeView}
                    objectViewsByApiName={workspace.objectViewsByApiName}
                    objects={objectQuery.items}
                    pageSize={PAGE_SIZE}
                    where={where}
                    dataVersion={dataVersion}
                  />
                </div>
                <ResultsRail
                  objectView={activeView}
                  objects={objectQuery.items}
                  isLoading={objectQuery.isLoading}
                  error={objectQuery.error}
                  onRetry={handleObjectsChanged}
                  selectedObjectId={null}
                  sort={sort}
                  onSortChange={(nextSort) => {
                    if (!activeExplorationId) return;
                    setSortByExplorationId((previous) => ({
                      ...previous,
                      [activeExplorationId]: nextSort,
                    }));
                    setPageCursor(null);
                  }}
                  onOpenObject={(ref) => setDetailStack([ref])}
                  onShowAllResults={() => setViewTab("results")}
                  onOpenLinkedType={(typeName, linkApiName) =>
                    handleOpenExploration(typeName, {
                      fromTypeName: activeView.apiName,
                      linkApiName,
                    })
                  }
                />
              </div>
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-auto bg-[#f6f8fa] p-4">
              <ResultsTable
                objectView={activeView}
                objects={objectQuery.items}
                isLoading={objectQuery.isLoading}
                error={objectQuery.error}
                onRetry={handleObjectsChanged}
                selectedObjectId={null}
                nextCursor={objectQuery.nextCursor}
                hasPreviousPage={pageCursor !== null}
                onNextPage={() => setPageCursor(objectQuery.nextCursor)}
                onFirstPage={() => setPageCursor(null)}
                onOpenObject={(ref) => setDetailStack([ref])}
              />
            </div>
          )}
        </>
      )}
      <ObjectSetDialog
        objectView={activeView}
        pills={pills}
        where={where}
        objects={objectQuery.items}
        isOpen={isObjectSetOpen}
        onOpenChange={setIsObjectSetOpen}
        onApplyPills={(nextPills) => applySnapshot(nextPills, appliedSearch)}
      />
      <ActionFormDialog
        actionView={pendingAction}
        targetObject={actionTarget}
        isOpen={pendingAction !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) setPendingAction(null);
        }}
        onApplied={handleObjectsChanged}
      />
    </div>
  );
}
