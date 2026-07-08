import type { StatusIntent } from "@/components/shared/StatusPill";

/**
 * Developer Console 백엔드는 snake_case JsonObject를 반환한다.
 * 화면에서 안전하게 읽기 위한 순수 정규화 헬퍼들.
 */

type Json = Record<string, unknown>;

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function strList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export const RESOURCE_TYPES = ["object", "link", "action", "function"] as const;
export type OsdkResourceType = (typeof RESOURCE_TYPES)[number];

/** scope 접미사 연산: 리소스 타입별 허용 오퍼레이션 (백엔드 scopes.py와 동일). */
export const OPERATIONS_BY_RESOURCE_TYPE: Record<OsdkResourceType, string[]> = {
  object: ["read", "subscribe"],
  action: ["validate", "execute"],
  link: ["read"],
  function: ["execute"],
};

export const RESOURCE_TYPE_LABEL: Record<OsdkResourceType, string> = {
  object: "객체 타입",
  link: "링크 타입",
  action: "액션 타입",
  function: "함수",
};

export function buildScope(
  resourceType: OsdkResourceType,
  resourceApiName: string,
  operation: string,
): string {
  return `osdk:${resourceType}:${resourceApiName}:${operation}`;
}

export type OsdkApplicationRow = {
  id: string;
  appApiName: string;
  displayName: string;
  status: string;
  ownerUserId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

export function toApplicationRow(raw: Json): OsdkApplicationRow {
  // list/get은 {application, clients, resources} envelope를, create.application은
  // application 레코드를 직접 준다. 둘 다 지원하기 위해 envelope를 벗겨낸다.
  const record =
    raw.application && typeof raw.application === "object"
      ? (raw.application as Json)
      : raw;
  return {
    id: str(record.id) ?? "",
    appApiName: str(record.app_api_name) ?? str(record.appApiName) ?? "",
    displayName: str(record.display_name) ?? str(record.displayName) ?? "",
    status: str(record.status) ?? "unknown",
    ownerUserId: str(record.owner_user_id) ?? str(record.ownerUserId),
    createdAt: str(record.created_at) ?? str(record.createdAt),
    updatedAt: str(record.updated_at) ?? str(record.updatedAt),
  };
}

export type OsdkClientRow = {
  id: string;
  clientId: string;
  status: string;
  redirectUris: string[];
  allowedScopes: string[];
  accessTokenTtlSeconds: number | null;
  refreshTokenTtlSeconds: number | null;
  clientSecret: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

export function toClientRow(raw: Json): OsdkClientRow {
  return {
    id: str(raw.id) ?? "",
    clientId: str(raw.client_id) ?? str(raw.clientId) ?? "",
    status: str(raw.status) ?? "unknown",
    redirectUris: strList(raw.redirect_uris ?? raw.redirectUris),
    allowedScopes: strList(raw.allowed_scopes ?? raw.allowedScopes),
    accessTokenTtlSeconds: num(
      raw.access_token_ttl_seconds ?? raw.accessTokenTtlSeconds,
    ),
    refreshTokenTtlSeconds: num(
      raw.refresh_token_ttl_seconds ?? raw.refreshTokenTtlSeconds,
    ),
    // 생성 시 1회만 노출되는 secret (있으면 노출, 재조회 불가).
    clientSecret: str(raw.client_secret) ?? str(raw.clientSecret),
    createdAt: str(raw.created_at) ?? str(raw.createdAt),
    updatedAt: str(raw.updated_at) ?? str(raw.updatedAt),
  };
}

export type OsdkResourceRow = {
  id: string;
  resourceType: OsdkResourceType | string;
  resourceApiName: string;
  scopes: string[];
};

export function toResourceRow(raw: Json): OsdkResourceRow {
  return {
    id: str(raw.id) ?? "",
    resourceType: str(raw.resource_type) ?? str(raw.resourceType) ?? "object",
    resourceApiName:
      str(raw.resource_api_name) ?? str(raw.resourceApiName) ?? "",
    scopes: strList(raw.scopes),
  };
}

export type OsdkSdkVersionRow = {
  id: string;
  language: string;
  packageName: string | null;
  version: string;
  generatorName: string | null;
  generatorVersion: string | null;
  ontologyFingerprint: string | null;
  compatibilityStatus: string;
  releaseStatus: string;
  artifactHash: string | null;
  artifactUri: string | null;
  createdAt: string | null;
  releasedAt: string | null;
};

export function toSdkVersionRow(raw: Json): OsdkSdkVersionRow {
  return {
    id: str(raw.id) ?? "",
    language: str(raw.language) ?? "unknown",
    packageName: str(raw.package_name) ?? str(raw.packageName),
    version: str(raw.version) ?? "",
    generatorName: str(raw.generator_name) ?? str(raw.generatorName),
    generatorVersion: str(raw.generator_version) ?? str(raw.generatorVersion),
    ontologyFingerprint:
      str(raw.ontology_fingerprint) ?? str(raw.ontologyFingerprint),
    compatibilityStatus:
      str(raw.compatibility_status) ??
      str(raw.compatibilityStatus) ??
      "unknown",
    releaseStatus:
      str(raw.release_status) ?? str(raw.releaseStatus) ?? "unknown",
    artifactHash: str(raw.artifact_hash) ?? str(raw.artifactHash),
    artifactUri: str(raw.artifact_uri) ?? str(raw.artifactUri),
    createdAt: str(raw.created_at) ?? str(raw.createdAt),
    releasedAt: str(raw.released_at) ?? str(raw.releasedAt),
  };
}

export type OsdkChannelRow = {
  id: string;
  language: string;
  channel: string;
  sdkVersionId: string;
  promotedAt: string | null;
  promotedByUserId: string | null;
};

export function toChannelRow(raw: Json): OsdkChannelRow {
  return {
    id: str(raw.id) ?? "",
    language: str(raw.language) ?? "unknown",
    channel: str(raw.channel) ?? "",
    sdkVersionId: str(raw.sdk_version_id) ?? str(raw.sdkVersionId) ?? "",
    promotedAt: str(raw.promoted_at) ?? str(raw.promotedAt),
    promotedByUserId: str(raw.promoted_by_user_id) ?? str(raw.promotedByUserId),
  };
}

export type OsdkCompatibilityWindowRow = {
  id: string;
  fromVersionId: string;
  toVersionId: string;
  supportedUntil: string | null;
  createdAt: string | null;
};

export function toCompatibilityWindowRow(
  raw: Json,
): OsdkCompatibilityWindowRow {
  return {
    id: str(raw.id) ?? "",
    fromVersionId: str(raw.from_version_id) ?? str(raw.fromVersionId) ?? "",
    toVersionId: str(raw.to_version_id) ?? str(raw.toVersionId) ?? "",
    supportedUntil: str(raw.supported_until) ?? str(raw.supportedUntil),
    createdAt: str(raw.created_at) ?? str(raw.createdAt),
  };
}

export type OsdkInstallMetadata = {
  sdkVersions: OsdkSdkVersionRow[];
  channels: OsdkChannelRow[];
  compatibilityWindows: OsdkCompatibilityWindowRow[];
};

export function toInstallMetadata(raw: Json): OsdkInstallMetadata {
  const versions = Array.isArray(raw.sdkVersions) ? raw.sdkVersions : [];
  const channels = Array.isArray(raw.channels) ? raw.channels : [];
  const windows = Array.isArray(raw.compatibilityWindows)
    ? raw.compatibilityWindows
    : [];
  return {
    sdkVersions: versions.map((item) => toSdkVersionRow(item as Json)),
    channels: channels.map((item) => toChannelRow(item as Json)),
    compatibilityWindows: windows.map((item) =>
      toCompatibilityWindowRow(item as Json),
    ),
  };
}

export type OsdkDownloadToken = {
  downloadToken: string;
  artifactId: string;
  expiresAt: string | null;
};

export function toDownloadToken(raw: Json): OsdkDownloadToken {
  return {
    downloadToken: str(raw.downloadToken) ?? "",
    artifactId: str(raw.artifactId) ?? "",
    expiresAt: str(raw.expiresAt),
  };
}

/** 상태 → StatusPill intent 매핑. */
export function statusIntent(status: string): StatusIntent {
  switch (status) {
    case "active":
    case "released":
    case "compatible_additive":
    case "compatible":
      return "success";
    case "inactive":
    case "deactivated":
      return "neutral";
    case "breaking":
    case "incompatible":
      return "danger";
    case "pending":
    case "draft":
      return "warning";
    default:
      return "info";
  }
}

export const CHANNEL_LABEL: Record<string, string> = {
  stable: "stable",
  beta: "beta",
  latest: "latest",
};

export function formatTimestamp(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
