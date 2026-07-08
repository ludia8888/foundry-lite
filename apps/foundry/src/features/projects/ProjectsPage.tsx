import { FolderSync, PanelRight, RotateCw, Star } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { DEMO_CONTEXT } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  CatalogCreateDialog,
  type CatalogCreateMode,
} from "./CatalogCreateDialog";
import { NewResourceMenu } from "./NewResourceMenu";
import { ProjectSidebar, type WorkspaceNav } from "./ProjectSidebar";
import { ResourceDetailDrawer } from "./ResourceDetailDrawer";
import { ResourceTable } from "./ResourceTable";
import type { ResourceKind, ResourceRow } from "./resource-model";
import { collectFolderIds, findFolderNode } from "./resource-model";
import {
  useResourceBrowser,
  type ResourceBrowserState,
  type ResourceSourceStatus,
} from "./use-resource-browser";

const TOP_TABS: ReadonlyArray<{
  id: string;
  label: string;
  isFuture: boolean;
}> = [
  { id: "catalog", label: "데이터 카탈로그", isFuture: true },
  { id: "projects", label: "프로젝트", isFuture: false },
  { id: "my-files", label: "내 파일", isFuture: true },
  { id: "shared", label: "공유됨", isFuture: true },
];

function TopTabStrip() {
  return (
    <div className="flex items-center border-b bg-card px-3">
      {TOP_TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          disabled={tab.isFuture}
          className={cn(
            "flex h-9 items-center gap-1.5 border-b-2 px-3 text-[12px]",
            tab.isFuture
              ? "cursor-not-allowed border-transparent text-muted-foreground/60"
              : "border-primary font-medium text-primary",
          )}
        >
          {tab.label}
          {tab.isFuture ? (
            <StatusPill intent="neutral">future</StatusPill>
          ) : null}
        </button>
      ))}
    </div>
  );
}

function SourceIssueStrips({ issues }: { issues: ResourceSourceStatus[] }) {
  if (issues.length === 0) return null;
  return (
    <div className="space-y-1.5">
      {issues.map((issue) => (
        <div
          key={issue.id}
          className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-warning/40 bg-warning/5 px-2.5 py-1.5 text-[12px]"
        >
          <span className="font-medium text-warning">
            {issue.label} 목록을 불러오지 못했습니다.
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            code={issue.error?.code}
            {issue.error?.requestId
              ? ` request_id=${issue.error.requestId}`
              : ""}
          </span>
          <Button
            size="sm"
            variant="outline"
            className="ml-auto h-6 px-2 text-[11px]"
            onClick={() => void issue.reload()}
          >
            <RotateCw className="size-3" />
            재시도
          </Button>
        </div>
      ))}
    </div>
  );
}

function CatalogGuideStrip({
  browser,
  onCreateProject,
}: {
  browser: ResourceBrowserState;
  onCreateProject: () => void;
}) {
  if (browser.activeProject === null) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground">
        <span>
          프로젝트가 없습니다. 새 프로젝트를 만들거나 카탈로그 동기화를 실행하면
          기존 리소스가 등록됩니다.
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            onClick={onCreateProject}
          >
            새 프로젝트
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={browser.isSyncing}
            onClick={() => void browser.syncCatalog()}
          >
            <FolderSync className="size-3" />
            {browser.isSyncing ? "동기화 중…" : "카탈로그 동기화"}
          </Button>
        </span>
      </div>
    );
  }
  if (browser.isCatalogEmpty) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground">
        <span>
          카탈로그에 등록된 리소스가 없습니다. 카탈로그 동기화를 실행하면 기존
          데이터셋·소스·파이프라인·온톨로지가 이 프로젝트에 등록됩니다.
        </span>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto h-6 px-2 text-[11px]"
          disabled={browser.isSyncing}
          onClick={() => void browser.syncCatalog()}
        >
          <FolderSync className="size-3" />
          {browser.isSyncing ? "동기화 중…" : "카탈로그 동기화"}
        </Button>
      </div>
    );
  }
  return null;
}

/** Projects & Resources: 카탈로그 API 기반 리소스 브라우저 (트리 + 테이블 + 상세 drawer). */
export default function ProjectsPage() {
  const browser = useResourceBrowser();
  const [activeNav, setActiveNav] = useState<WorkspaceNav>("files");
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [selectedRid, setSelectedRid] = useState<string | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(true);
  const [searchText, setSearchText] = useState("");
  const [kindFilter, setKindFilter] = useState<ResourceKind | "all">("all");
  const [isFavoriteOnly, setIsFavoriteOnly] = useState(false);
  const [createDialogMode, setCreateDialogMode] =
    useState<CatalogCreateMode | null>(null);

  const isTrashView = activeNav === "trash";

  const folderIdFilter = useMemo(() => {
    if (selectedFolderId === null) return null;
    const node = findFolderNode(browser.tree, selectedFolderId);
    return new Set(node ? collectFolderIds(node) : [selectedFolderId]);
  }, [browser.tree, selectedFolderId]);

  const visibleRows = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    const baseRows = isTrashView ? browser.trashedRows : browser.rows;
    return baseRows.filter((row) => {
      if (
        !isTrashView &&
        folderIdFilter !== null &&
        (row.folderId === null || !folderIdFilter.has(row.folderId))
      ) {
        return false;
      }
      if (kindFilter !== "all" && row.kind !== kindFilter) return false;
      if (isFavoriteOnly && !row.isFavorite) return false;
      if (
        query !== "" &&
        !row.name.toLowerCase().includes(query) &&
        !row.rid.toLowerCase().includes(query)
      ) {
        return false;
      }
      return true;
    });
  }, [
    browser.rows,
    browser.trashedRows,
    isTrashView,
    folderIdFilter,
    kindFilter,
    isFavoriteOnly,
    searchText,
  ]);

  const selectedRow = useMemo(
    () =>
      [...browser.rows, ...browser.trashedRows].find(
        (row) => row.rid === selectedRid,
      ) ?? null,
    [browser.rows, browser.trashedRows, selectedRid],
  );

  useEffect(() => {
    if (selectedRid === null) return;
    if (visibleRows.some((row) => row.rid === selectedRid)) return;
    setSelectedRid(null);
  }, [selectedRid, visibleRows]);

  const objectTypeApiNames = useMemo(
    () =>
      browser.rows
        .filter((row) => row.kind === "object_type" && row.ontologyApiName)
        .map((row) => row.ontologyApiName as string),
    [browser.rows],
  );

  const handleSelectRow = (row: ResourceRow) => {
    setSelectedRid(row.rid);
    setIsDrawerOpen(true);
  };

  const handleSelectProject = (projectId: string) => {
    browser.selectProject(projectId);
    setSelectedFolderId(null);
    setSelectedRid(null);
    setActiveNav("files");
  };

  const hasAnyRows = browser.rows.length > 0;
  const isEverySourceFailed =
    browser.failedSourceStatuses.length === browser.sourceStatuses.length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopTabStrip />
      <div className="border-b bg-card px-4 py-2.5">
        <PageHeader
          title={browser.activeProject?.displayName ?? "프로젝트 없음"}
          description="프로젝트를 열고 폴더에서 리소스를 찾아 새 데이터/파이프라인/앱을 만듭니다."
          meta={
            <>
              <Star className="size-3.5 fill-warning text-warning" />
              <StatusPill intent="info">{DEMO_CONTEXT.tenantId}</StatusPill>
              <span className="font-mono text-[11px] text-muted-foreground">
                리소스 {browser.rows.length}개
              </span>
            </>
          }
          actions={
            <>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-[12px]"
                disabled={browser.isRefreshing}
                onClick={() => void browser.reloadAll()}
              >
                <RotateCw
                  className={cn(
                    "size-3.5",
                    browser.isRefreshing && "animate-spin",
                  )}
                />
                새로고침
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-[12px]"
                disabled={browser.isSyncing}
                onClick={() => void browser.syncCatalog()}
              >
                <FolderSync className="size-3.5" />
                {browser.isSyncing ? "동기화 중…" : "카탈로그 동기화"}
              </Button>
              <NewResourceMenu
                objectTypeApiNames={objectTypeApiNames}
                onCreated={() => void browser.reloadAll()}
                onCreateProject={() => setCreateDialogMode("project")}
                onCreateFolder={() => setCreateDialogMode("folder")}
                canCreateFolder={browser.activeProject !== null}
              />
              <Button
                size="sm"
                variant="ghost"
                className="size-7 p-0"
                aria-label="상세 패널 토글"
                onClick={() => setIsDrawerOpen((previous) => !previous)}
              >
                <PanelRight className="size-4" />
              </Button>
            </>
          }
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <ProjectSidebar
          projects={browser.projects}
          activeProject={browser.activeProject}
          onSelectProject={handleSelectProject}
          onCreateProject={() => setCreateDialogMode("project")}
          onCreateFolder={() => setCreateDialogMode("folder")}
          tree={browser.tree}
          selectedFolderId={selectedFolderId}
          onSelectFolder={(folderId) => {
            setSelectedFolderId(folderId);
            setActiveNav("files");
          }}
          onOpenCatalog={() => {
            setSelectedFolderId(null);
            setActiveNav("files");
          }}
          activeNav={activeNav}
          onSelectNav={setActiveNav}
          trashCount={browser.trashedRows.length}
          totalCount={browser.rows.length}
          isLoading={browser.isInitialLoading}
        />

        <div className="flex min-w-0 flex-1 flex-col gap-2 p-3">
          {!isTrashView ? (
            <CatalogGuideStrip
              browser={browser}
              onCreateProject={() => setCreateDialogMode("project")}
            />
          ) : null}
          <SourceIssueStrips issues={browser.failedSourceStatuses} />
          {browser.isInitialLoading ? (
            <LoadingState rowCount={8} />
          ) : !hasAnyRows && isEverySourceFailed ? (
            <ErrorState
              error={browser.failedSourceStatuses[0]?.error}
              onRetry={() => void browser.reloadAll()}
            />
          ) : !hasAnyRows && !isTrashView ? (
            <EmptyState
              title="아직 리소스가 없습니다"
              description="새로 만들기 메뉴에서 파이프라인 브랜치·객체 세트·OSDK 앱을 만들거나, 데이터 연결에서 소스를 온보딩하세요."
              action={
                <NewResourceMenu
                  objectTypeApiNames={objectTypeApiNames}
                  onCreated={() => void browser.reloadAll()}
                  onCreateProject={() => setCreateDialogMode("project")}
                  onCreateFolder={() => setCreateDialogMode("folder")}
                  canCreateFolder={browser.activeProject !== null}
                />
              }
            />
          ) : (
            <ResourceTable
              rows={visibleRows}
              selectedRid={selectedRid}
              onSelectRow={handleSelectRow}
              pendingRids={browser.pendingRids}
              onToggleFavorite={(rid) => void browser.toggleFavorite(rid)}
              isTrashView={isTrashView}
              onRestore={(rid) => void browser.restoreFromTrash(rid)}
              searchText={searchText}
              onSearchTextChange={setSearchText}
              kindFilter={kindFilter}
              onKindFilterChange={setKindFilter}
              isFavoriteOnly={isFavoriteOnly}
              onToggleFavoriteOnly={() =>
                setIsFavoriteOnly((previous) => !previous)
              }
            />
          )}
        </div>

        {isDrawerOpen && selectedRow ? (
          <ResourceDetailDrawer
            row={selectedRow}
            project={browser.activeProject}
            folders={browser.folders}
            isPending={browser.pendingRids.has(selectedRow.rid)}
            onToggleFavorite={(rid) => void browser.toggleFavorite(rid)}
            onMoveToTrash={(rid) => void browser.moveToTrash(rid)}
            onRestore={(rid) => void browser.restoreFromTrash(rid)}
            onMoveToFolder={(rid, folderId) =>
              void browser.moveToFolder(rid, folderId)
            }
            onClose={() => setIsDrawerOpen(false)}
          />
        ) : null}
      </div>

      <CatalogCreateDialog
        mode={createDialogMode}
        activeProject={browser.activeProject}
        folders={browser.folders}
        onClose={() => setCreateDialogMode(null)}
        onCreated={(created) => {
          if (created.mode === "project") {
            browser.selectProject(created.id);
            setSelectedFolderId(null);
          }
          void browser.reloadAll();
        }}
      />
    </div>
  );
}
