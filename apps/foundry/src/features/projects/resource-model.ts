import type {
  Dataset,
  ObjectSetPayload,
  OntologyCatalog,
  OsdkApplication,
  PipelineBranch,
  ProjectFolder,
  ResourceItem,
  SourceConnection,
} from "@foundry-lite/sdk";

/**
 * 리소스 브라우저가 다루는 타입.
 * 카탈로그(/api/resources) 등록 리소스와, 카탈로그가 아직 커버하지 못하는
 * 합성 surface 리소스(객체 타입·객체 세트·OSDK 앱 등)를 함께 표기한다.
 */
export type ResourceKind =
  | "dataset"
  | "source"
  | "pipeline_branch"
  | "object_type"
  | "link_type"
  | "object_set"
  | "osdk_app"
  | "ontology"
  | "registered";

export interface ResourceKindMeta {
  label: string;
  sourceSurface: string;
  surfaceRoute: string;
}

export const RESOURCE_KIND_META: Record<ResourceKind, ResourceKindMeta> = {
  dataset: {
    label: "데이터셋",
    sourceSurface: "Dataset / Media Set",
    surfaceRoute: "/datasets",
  },
  source: {
    label: "소스 연결",
    sourceSurface: "Data Connection",
    surfaceRoute: "/data/connections",
  },
  pipeline_branch: {
    label: "파이프라인 브랜치",
    sourceSurface: "Pipeline Builder",
    surfaceRoute: "/pipelines",
  },
  object_type: {
    label: "객체 타입",
    sourceSurface: "Ontology Manager",
    surfaceRoute: "/ontology",
  },
  link_type: {
    label: "링크 타입",
    sourceSurface: "Ontology Manager",
    surfaceRoute: "/ontology",
  },
  object_set: {
    label: "객체 세트",
    sourceSurface: "Object Explorer",
    surfaceRoute: "/objects",
  },
  osdk_app: {
    label: "OSDK 앱",
    sourceSurface: "Developer Console",
    surfaceRoute: "/developer",
  },
  ontology: {
    label: "온톨로지 버전",
    sourceSurface: "Ontology Manager",
    surfaceRoute: "/ontology",
  },
  registered: {
    label: "등록 리소스",
    sourceSurface: "Resource Catalog",
    surfaceRoute: "/projects",
  },
};

export type ResourceOrigin = "catalog" | "surface";

export interface ResourceRow {
  rid: string;
  name: string;
  kind: ResourceKind;
  origin: ResourceOrigin;
  projectId: string | null;
  folderId: string | null;
  folderLabel: string;
  updatedAt: string | null;
  status: string | null;
  isFavorite: boolean;
  isTrashed: boolean;
  description: string | null;
  sourceSurface?: string;
  sourceRef?: string;
  operationsPath?: string | null;
  /** 카탈로그 항목과의 중복 제거 키 (`${sourceSurface}:${sourceRef}`). */
  surfaceKey?: string;
  datasetNamespace?: string;
  datasetName?: string;
  ontologyResourceType?: "object_type" | "link_type";
  ontologyApiName?: string;
}

export interface FolderNode {
  id: string;
  label: string;
  count: number;
  children: FolderNode[];
}

const SURFACE_ROW_DEFAULTS = {
  origin: "surface" as const,
  projectId: null,
  folderId: null,
  folderLabel: "카탈로그 미등록",
  isFavorite: false,
  isTrashed: false,
};

const KIND_BY_RESOURCE_TYPE: Record<string, ResourceKind> = {
  dataset: "dataset",
  source: "source",
  pipeline: "pipeline_branch",
  ontology: "ontology",
};

function readString(
  record: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function splitDatasetRef(
  sourceRef: string,
): { datasetNamespace: string; datasetName: string } | null {
  const dotIndex = sourceRef.indexOf(".");
  if (dotIndex <= 0 || dotIndex === sourceRef.length - 1) return null;
  return {
    datasetNamespace: sourceRef.slice(0, dotIndex),
    datasetName: sourceRef.slice(dotIndex + 1),
  };
}

export function catalogSurfaceKey(item: ResourceItem): string {
  return `${item.sourceSurface}:${item.sourceRef}`;
}

/** 카탈로그(/api/resources) 항목을 브라우저 행으로 변환한다. */
export function toCatalogRows(
  items: readonly ResourceItem[],
  foldersById: ReadonlyMap<string, ProjectFolder>,
): ResourceRow[] {
  return items.map((item) => {
    const kind = KIND_BY_RESOURCE_TYPE[item.resourceType] ?? "registered";
    const folder =
      item.folderId !== null ? foldersById.get(item.folderId) : undefined;
    const datasetRef =
      kind === "dataset" ? splitDatasetRef(item.sourceRef) : null;
    return {
      rid: item.rid,
      name: item.displayName,
      kind,
      origin: "catalog" as const,
      projectId: item.projectId,
      folderId: item.folderId,
      folderLabel: folder ? folder.displayName : "최상위",
      updatedAt: item.updatedAt,
      status: item.status,
      isFavorite: item.isFavorite,
      isTrashed: item.status === "trashed",
      description: null,
      sourceSurface: item.sourceSurface,
      sourceRef: item.sourceRef,
      operationsPath: item.operationsPath,
      ...(datasetRef ?? {}),
    };
  });
}

export function toDatasetRows(datasets: readonly Dataset[]): ResourceRow[] {
  return datasets.map((dataset) => ({
    ...SURFACE_ROW_DEFAULTS,
    rid: dataset.id,
    name: `${dataset.namespace}.${dataset.name}`,
    kind: "dataset" as const,
    updatedAt: dataset.updated_at,
    status: dataset.status,
    description: dataset.description,
    surfaceKey: `dataset:${dataset.namespace}.${dataset.name}`,
    datasetNamespace: dataset.namespace,
    datasetName: dataset.name,
  }));
}

export function toSourceRows(
  sources: readonly SourceConnection[],
): ResourceRow[] {
  return sources.map((source) => ({
    ...SURFACE_ROW_DEFAULTS,
    rid: source.sourceName,
    name: source.displayName || source.sourceName,
    kind: "source" as const,
    updatedAt: source.updatedAt,
    status: source.status,
    description: source.targetDatasetRef
      ? `대상 데이터셋: ${source.targetDatasetRef}`
      : null,
    surfaceKey: `source:${source.sourceName}`,
  }));
}

export function toPipelineBranchRows(
  branches: readonly PipelineBranch[],
): ResourceRow[] {
  return branches.map((branch) => ({
    ...SURFACE_ROW_DEFAULTS,
    rid: branch.id,
    name: readString(branch, ["name"]) ?? branch.id,
    kind: "pipeline_branch" as const,
    updatedAt: readString(branch, ["updatedAt", "createdAt"]),
    status: readString(branch, ["status"]),
    description: `파이프라인: ${branch.pipelineId}`,
    surfaceKey: `pipeline_branch:${branch.id}`,
  }));
}

export function toOntologyRows(catalog: OntologyCatalog): ResourceRow[] {
  const objectRows = catalog.objectTypes.map((objectType) => ({
    ...SURFACE_ROW_DEFAULTS,
    rid: objectType.apiName,
    name: objectType.displayName || objectType.apiName,
    kind: "object_type" as const,
    updatedAt: catalog.activatedAt ?? catalog.createdAt,
    status: catalog.status,
    description: `온톨로지 버전 v${catalog.versionNumber}`,
    ontologyResourceType: "object_type" as const,
    ontologyApiName: objectType.apiName,
  }));
  const linkRows = catalog.linkTypes.map((linkType) => ({
    ...SURFACE_ROW_DEFAULTS,
    rid: linkType.apiName,
    name: linkType.displayName || linkType.apiName,
    kind: "link_type" as const,
    updatedAt: catalog.activatedAt ?? catalog.createdAt,
    status: catalog.status,
    description: `온톨로지 버전 v${catalog.versionNumber}`,
    ontologyResourceType: "link_type" as const,
    ontologyApiName: linkType.apiName,
  }));
  return [...objectRows, ...linkRows];
}

export function toObjectSetRows(
  objectSets: readonly ObjectSetPayload[],
): ResourceRow[] {
  return objectSets.map((objectSet) => ({
    ...SURFACE_ROW_DEFAULTS,
    rid: objectSet.id,
    name: objectSet.name,
    kind: "object_set" as const,
    updatedAt: objectSet.createdAt,
    status: objectSet.lifecycle,
    description: `${objectSet.objectType} · ${objectSet.setType} · ${objectSet.objectIds.length}건`,
  }));
}

export function toOsdkAppRows(apps: readonly OsdkApplication[]): ResourceRow[] {
  return apps.map((app) => {
    const record = app.application;
    const rid =
      readString(record, ["id", "appId", "app_id"]) ??
      readString(record, ["appApiName", "app_api_name"]) ??
      "osdk-app";
    return {
      ...SURFACE_ROW_DEFAULTS,
      rid,
      name:
        readString(record, ["displayName", "display_name"]) ??
        readString(record, ["appApiName", "app_api_name"]) ??
        rid,
      kind: "osdk_app" as const,
      updatedAt: readString(record, [
        "updatedAt",
        "updated_at",
        "createdAt",
        "created_at",
      ]),
      status: readString(record, ["status"]),
      description: `클라이언트 ${app.clients.length}개 · 리소스 ${app.resources.length}개`,
    };
  });
}

/** 서버 폴더 목록(parentFolderId 트리)에서 사이드바 폴더 트리를 만든다. */
export function buildFolderTree(
  folders: readonly ProjectFolder[],
  rows: readonly ResourceRow[],
): FolderNode[] {
  const activeFolders = folders.filter((folder) => folder.status === "active");
  const countsByFolderId = new Map<string, number>();
  for (const row of rows) {
    if (row.folderId === null) continue;
    countsByFolderId.set(
      row.folderId,
      (countsByFolderId.get(row.folderId) ?? 0) + 1,
    );
  }
  const childrenByParentId = new Map<string | null, ProjectFolder[]>();
  for (const folder of activeFolders) {
    const siblings = childrenByParentId.get(folder.parentFolderId) ?? [];
    childrenByParentId.set(folder.parentFolderId, [...siblings, folder]);
  }
  const buildNode = (folder: ProjectFolder): FolderNode => {
    const children = (childrenByParentId.get(folder.id) ?? []).map(buildNode);
    const childCount = children.reduce((sum, child) => sum + child.count, 0);
    return {
      id: folder.id,
      label: folder.displayName,
      count: (countsByFolderId.get(folder.id) ?? 0) + childCount,
      children,
    };
  };
  return (childrenByParentId.get(null) ?? []).map(buildNode);
}

export function findFolderNode(
  tree: readonly FolderNode[],
  folderId: string,
): FolderNode | null {
  for (const node of tree) {
    if (node.id === folderId) return node;
    const found = findFolderNode(node.children, folderId);
    if (found !== null) return found;
  }
  return null;
}

/** 폴더 노드와 그 하위 폴더 id를 모두 수집한다 (폴더 필터용). */
export function collectFolderIds(node: FolderNode): string[] {
  return [node.id, ...node.children.flatMap(collectFolderIds)];
}

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 16).replace("T", " ");
}

export function formatByteSize(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
