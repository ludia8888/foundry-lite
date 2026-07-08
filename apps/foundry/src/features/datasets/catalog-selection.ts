/** 좌측 카탈로그 선택 상태. dataset은 version drill-down까지 포함한다. */
export type CatalogSelection =
  | { kind: "dataset"; namespace: string; name: string; version?: string }
  | { kind: "media"; mediaSetId: string }
  | { kind: "media-new" };
