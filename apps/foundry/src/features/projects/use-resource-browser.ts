import type {
  FoundryLiteApiError,
  ProjectFolder,
  ResourceProject,
} from "@foundry-lite/sdk";
import {
  createFoundryLiteClient,
  idempotencyKey,
  normalizeFoundryLiteError,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { API_BASE_URL, DEMO_CONTEXT } from "@/lib/api";
import { isActiveOntologyMissingError } from "@/lib/errors";

import { useProjectsQuery } from "./use-projects-query";

import type { FolderNode, ResourceRow } from "./resource-model";
import {
  buildFolderTree,
  catalogSurfaceKey,
  toCatalogRows,
  toDatasetRows,
  toObjectSetRows,
  toOntologyRows,
  toOsdkAppRows,
  toPipelineBranchRows,
  toSourceRows,
} from "./resource-model";

export interface ResourceSourceStatus {
  id: string;
  label: string;
  isLoading: boolean;
  error: FoundryLiteApiError | null;
  reload: () => Promise<unknown>;
}

export interface ResourceBrowserState {
  projects: ResourceProject[];
  activeProject: ResourceProject | null;
  selectProject: (projectId: string) => void;
  folders: ProjectFolder[];
  rows: ResourceRow[];
  trashedRows: ResourceRow[];
  tree: FolderNode[];
  sourceStatuses: ResourceSourceStatus[];
  failedSourceStatuses: ResourceSourceStatus[];
  isInitialLoading: boolean;
  isRefreshing: boolean;
  isCatalogEmpty: boolean;
  isSyncing: boolean;
  pendingRids: ReadonlySet<string>;
  toggleFavorite: (rid: string) => Promise<void>;
  moveToTrash: (rid: string) => Promise<void>;
  restoreFromTrash: (rid: string) => Promise<void>;
  moveToFolder: (rid: string, folderId: string | null) => Promise<void>;
  syncCatalog: () => Promise<void>;
  reloadAll: () => Promise<void>;
}

function withoutKey(
  record: Readonly<Record<string, boolean | string>>,
  key: string,
): Record<string, never> {
  const { [key]: _removed, ...rest } = record;
  return rest as Record<string, never>;
}

/**
 * Compass형 리소스 브라우저 데이터 훅.
 * 프로젝트/폴더/리소스는 실제 카탈로그 API(/api/projects, /api/resources)를 쓰고,
 * 카탈로그가 커버하지 못하는 surface(객체 타입·객체 세트·OSDK 앱 등)는
 * 기존 6-surface 합성 목록으로 보충해 하나의 테이블로 합친다.
 * 즐겨찾기·휴지통은 서버 API(favorites.set/delete, items.trash/restore)에
 * optimistic update로 연동한다.
 */
export function useResourceBrowser(): ResourceBrowserState {
  const client = useFoundryLiteClient();
  /** resources.admin.reconcile은 admin role이 필요해 데모 컨텍스트에 admin을 더한 전용 클라이언트를 쓴다. */
  const adminClient = useMemo(
    () =>
      createFoundryLiteClient({
        baseUrl: API_BASE_URL,
        context: {
          ...DEMO_CONTEXT,
          roles: [...(DEMO_CONTEXT.roles ?? []), "admin"],
        },
      }),
    [],
  );

  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [pendingRids, setPendingRids] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [favoriteOverridesByRid, setFavoriteOverridesByRid] = useState<
    Readonly<Record<string, boolean>>
  >({});
  const [statusOverridesByRid, setStatusOverridesByRid] = useState<
    Readonly<Record<string, string>>
  >({});
  const [isSyncing, setIsSyncing] = useState(false);

  const loadProjects = useCallback(
    () => client.resources.projects.list(),
    [client],
  );
  const projectsQuery = useProjectsQuery(
    ["projects", "catalog-projects"],
    loadProjects,
  );

  const projects = useMemo(
    () => projectsQuery.data ?? [],
    [projectsQuery.data],
  );
  const activeProject = useMemo(
    () =>
      projects.find((project) => project.id === selectedProjectId) ??
      projects[0] ??
      null,
    [projects, selectedProjectId],
  );
  const activeProjectId = activeProject?.id ?? null;

  const loadFolders = useCallback(
    () =>
      activeProjectId === null
        ? Promise.resolve([] as ProjectFolder[])
        : client.resources.folders.list(activeProjectId),
    [client, activeProjectId],
  );
  const foldersQuery = useProjectsQuery(
    ["projects", "catalog-folders", activeProjectId],
    loadFolders,
  );

  const loadItems = useCallback(
    () =>
      activeProjectId === null
        ? Promise.resolve({ items: [], nextCursor: null })
        : client.resources.items.search({
            projectId: activeProjectId,
            includeTrashed: true,
          }),
    [client, activeProjectId],
  );
  const itemsQuery = useProjectsQuery(
    ["projects", "catalog-items", activeProjectId],
    loadItems,
  );

  const loadDatasets = useCallback(() => client.datasets.list(), [client]);
  const loadSources = useCallback(() => client.sources.list(), [client]);
  const loadPipelineBranches = useCallback(
    () => client.pipelines.branches.list({ limit: 100 }),
    [client],
  );
  const loadCatalog = useCallback(() => client.ontology.catalog(), [client]);
  const loadObjectSets = useCallback(() => client.objectSets.list(), [client]);
  const loadOsdkApps = useCallback(
    () => client.developerConsole.osdkApplications.list(),
    [client],
  );

  const datasetsQuery = useProjectsQuery(
    ["projects", "datasets"],
    loadDatasets,
  );
  const sourcesQuery = useProjectsQuery(["projects", "sources"], loadSources);
  const branchesQuery = useProjectsQuery(
    ["projects", "pipeline-branches"],
    loadPipelineBranches,
  );
  const catalogQuery = useProjectsQuery(
    ["projects", "ontology-catalog"],
    loadCatalog,
  );
  const objectSetsQuery = useProjectsQuery(
    ["projects", "object-sets"],
    loadObjectSets,
  );
  const osdkAppsQuery = useProjectsQuery(
    ["projects", "osdk-apps"],
    loadOsdkApps,
  );

  const folders = useMemo(() => foldersQuery.data ?? [], [foldersQuery.data]);
  const foldersById = useMemo(
    () => new Map(folders.map((folder) => [folder.id, folder])),
    [folders],
  );

  const catalogRows = useMemo<ResourceRow[]>(() => {
    const baseRows = toCatalogRows(itemsQuery.data?.items ?? [], foldersById);
    return baseRows.map((row) => {
      const favoriteOverride = favoriteOverridesByRid[row.rid];
      const statusOverride = statusOverridesByRid[row.rid];
      if (favoriteOverride === undefined && statusOverride === undefined) {
        return row;
      }
      const nextStatus = statusOverride ?? row.status;
      return {
        ...row,
        isFavorite: favoriteOverride ?? row.isFavorite,
        status: nextStatus,
        isTrashed: nextStatus === "trashed",
      };
    });
  }, [
    itemsQuery.data,
    foldersById,
    favoriteOverridesByRid,
    statusOverridesByRid,
  ]);

  const coveredSurfaceKeys = useMemo(
    () => new Set((itemsQuery.data?.items ?? []).map(catalogSurfaceKey)),
    [itemsQuery.data],
  );

  const surfaceRows = useMemo<ResourceRow[]>(
    () =>
      [
        ...toDatasetRows(datasetsQuery.data ?? []),
        ...toSourceRows(sourcesQuery.data ?? []),
        ...toPipelineBranchRows(branchesQuery.data?.items ?? []),
        ...(catalogQuery.data ? toOntologyRows(catalogQuery.data) : []),
        ...toObjectSetRows(objectSetsQuery.data?.items ?? []),
        ...toOsdkAppRows(osdkAppsQuery.data ?? []),
      ].filter(
        (row) =>
          row.surfaceKey === undefined ||
          !coveredSurfaceKeys.has(row.surfaceKey),
      ),
    [
      datasetsQuery.data,
      sourcesQuery.data,
      branchesQuery.data,
      catalogQuery.data,
      objectSetsQuery.data,
      osdkAppsQuery.data,
      coveredSurfaceKeys,
    ],
  );

  const rows = useMemo<ResourceRow[]>(
    () => [...catalogRows.filter((row) => !row.isTrashed), ...surfaceRows],
    [catalogRows, surfaceRows],
  );
  const trashedRows = useMemo(
    () => catalogRows.filter((row) => row.isTrashed),
    [catalogRows],
  );
  const tree = useMemo(() => buildFolderTree(folders, rows), [folders, rows]);

  const ontologyCatalogError = isActiveOntologyMissingError(catalogQuery.error)
    ? null
    : catalogQuery.error;
  const sourceStatuses = useMemo<ResourceSourceStatus[]>(
    () => [
      {
        id: "catalog-projects",
        label: "프로젝트",
        isLoading: projectsQuery.isLoading,
        error: projectsQuery.error,
        reload: projectsQuery.reload,
      },
      {
        id: "catalog-folders",
        label: "폴더",
        isLoading: foldersQuery.isLoading,
        error: foldersQuery.error,
        reload: foldersQuery.reload,
      },
      {
        id: "catalog-items",
        label: "리소스 카탈로그",
        isLoading: itemsQuery.isLoading,
        error: itemsQuery.error,
        reload: itemsQuery.reload,
      },
      {
        id: "datasets",
        label: "데이터셋",
        isLoading: datasetsQuery.isLoading,
        error: datasetsQuery.error,
        reload: datasetsQuery.reload,
      },
      {
        id: "sources",
        label: "소스 연결",
        isLoading: sourcesQuery.isLoading,
        error: sourcesQuery.error,
        reload: sourcesQuery.reload,
      },
      {
        id: "pipelines",
        label: "파이프라인 브랜치",
        isLoading: branchesQuery.isLoading,
        error: branchesQuery.error,
        reload: branchesQuery.reload,
      },
      {
        id: "ontology",
        label: "온톨로지 카탈로그",
        isLoading: catalogQuery.isLoading,
        error: ontologyCatalogError,
        reload: catalogQuery.reload,
      },
      {
        id: "object-sets",
        label: "객체 세트",
        isLoading: objectSetsQuery.isLoading,
        error: objectSetsQuery.error,
        reload: objectSetsQuery.reload,
      },
      {
        id: "osdk-apps",
        label: "OSDK 앱",
        isLoading: osdkAppsQuery.isLoading,
        error: osdkAppsQuery.error,
        reload: osdkAppsQuery.reload,
      },
    ],
    [
      projectsQuery.isLoading,
      projectsQuery.error,
      projectsQuery.reload,
      foldersQuery.isLoading,
      foldersQuery.error,
      foldersQuery.reload,
      itemsQuery.isLoading,
      itemsQuery.error,
      itemsQuery.reload,
      datasetsQuery.isLoading,
      datasetsQuery.error,
      datasetsQuery.reload,
      sourcesQuery.isLoading,
      sourcesQuery.error,
      sourcesQuery.reload,
      branchesQuery.isLoading,
      branchesQuery.error,
      branchesQuery.reload,
      catalogQuery.isLoading,
      ontologyCatalogError,
      catalogQuery.reload,
      objectSetsQuery.isLoading,
      objectSetsQuery.error,
      objectSetsQuery.reload,
      osdkAppsQuery.isLoading,
      osdkAppsQuery.error,
      osdkAppsQuery.reload,
    ],
  );

  const reloadAll = useCallback(async () => {
    await Promise.all(sourceStatuses.map((status) => status.reload()));
  }, [sourceStatuses]);

  const markPending = useCallback((rid: string, isPending: boolean) => {
    setPendingRids((previous) => {
      const next = new Set(previous);
      if (isPending) next.add(rid);
      else next.delete(rid);
      return next;
    });
  }, []);

  const toggleFavorite = useCallback(
    async (rid: string) => {
      const row = catalogRows.find((candidate) => candidate.rid === rid);
      if (!row) return;
      const nextFavorite = !row.isFavorite;
      markPending(rid, true);
      setFavoriteOverridesByRid((previous) => ({
        ...previous,
        [rid]: nextFavorite,
      }));
      try {
        if (nextFavorite) {
          await client.resources.favorites.set(rid, {
            idempotencyKey: idempotencyKey("resources.favorites.set", rid),
          });
        } else {
          await client.resources.favorites.delete(rid, {
            idempotencyKey: idempotencyKey("resources.favorites.delete", rid),
          });
        }
        await itemsQuery.reload();
      } catch (caught) {
        const error = normalizeFoundryLiteError(caught);
        toast.error(`즐겨찾기 변경 실패 — code=${error.code}`);
      } finally {
        setFavoriteOverridesByRid((previous) => withoutKey(previous, rid));
        markPending(rid, false);
      }
    },
    [catalogRows, client, itemsQuery.reload, markPending],
  );

  const setResourceStatus = useCallback(
    async (
      rid: string,
      nextStatus: "active" | "trashed",
      mutate: () => Promise<unknown>,
      failureMessage: string,
    ) => {
      markPending(rid, true);
      setStatusOverridesByRid((previous) => ({
        ...previous,
        [rid]: nextStatus,
      }));
      try {
        await mutate();
        await itemsQuery.reload();
      } catch (caught) {
        const error = normalizeFoundryLiteError(caught);
        toast.error(`${failureMessage} — code=${error.code}`);
      } finally {
        setStatusOverridesByRid((previous) => withoutKey(previous, rid));
        markPending(rid, false);
      }
    },
    [itemsQuery.reload, markPending],
  );

  const moveToTrash = useCallback(
    (rid: string) =>
      setResourceStatus(
        rid,
        "trashed",
        () =>
          client.resources.items.trash(rid, {
            idempotencyKey: idempotencyKey("resources.items.trash", rid),
          }),
        "휴지통 이동 실패",
      ),
    [client, setResourceStatus],
  );

  const restoreFromTrash = useCallback(
    (rid: string) =>
      setResourceStatus(
        rid,
        "active",
        () =>
          client.resources.items.restore(rid, {
            idempotencyKey: idempotencyKey("resources.items.restore", rid),
          }),
        "복원 실패",
      ),
    [client, setResourceStatus],
  );

  const moveToFolder = useCallback(
    async (rid: string, folderId: string | null) => {
      const row = catalogRows.find((candidate) => candidate.rid === rid);
      const projectId = row?.projectId ?? activeProjectId;
      if (projectId === null) return;
      markPending(rid, true);
      try {
        await client.resources.items.move(
          rid,
          { projectId, folderId },
          {
            idempotencyKey: idempotencyKey("resources.items.move", rid),
          },
        );
        await itemsQuery.reload();
        const folderName =
          folderId === null
            ? "최상위"
            : (foldersById.get(folderId)?.displayName ?? folderId);
        toast.success(`리소스를 ${folderName} 폴더로 이동했습니다.`);
      } catch (caught) {
        const error = normalizeFoundryLiteError(caught);
        toast.error(`폴더 이동 실패 — code=${error.code}`);
      } finally {
        markPending(rid, false);
      }
    },
    [
      activeProjectId,
      catalogRows,
      client,
      foldersById,
      itemsQuery.reload,
      markPending,
    ],
  );

  const syncCatalog = useCallback(async () => {
    setIsSyncing(true);
    try {
      const result = await adminClient.resources.admin.reconcile(
        { projectId: activeProjectId },
        {
          idempotencyKey: idempotencyKey(
            "resources.admin.reconcile",
            activeProjectId ?? "personal",
          ),
        },
      );
      toast.success(
        `카탈로그 동기화 완료 — ${result.createdOrUpdated}건 등록/갱신`,
      );
      await Promise.all([
        projectsQuery.reload(),
        foldersQuery.reload(),
        itemsQuery.reload(),
      ]);
    } catch (caught) {
      const error = normalizeFoundryLiteError(caught);
      toast.error(`카탈로그 동기화 실패 — code=${error.code}`);
    } finally {
      setIsSyncing(false);
    }
  }, [
    adminClient,
    activeProjectId,
    projectsQuery.reload,
    foldersQuery.reload,
    itemsQuery.reload,
  ]);

  return {
    projects,
    activeProject,
    selectProject: setSelectedProjectId,
    folders,
    rows,
    trashedRows,
    tree,
    sourceStatuses,
    failedSourceStatuses: sourceStatuses.filter(
      (status) => status.error !== null,
    ),
    isInitialLoading:
      rows.length === 0 && sourceStatuses.some((status) => status.isLoading),
    isRefreshing: sourceStatuses.some((status) => status.isLoading),
    isCatalogEmpty:
      activeProject !== null &&
      itemsQuery.data !== null &&
      itemsQuery.data.items.length === 0,
    isSyncing,
    pendingRids,
    toggleFavorite,
    moveToTrash,
    restoreFromTrash,
    moveToFolder,
    syncCatalog,
    reloadAll,
  };
}
