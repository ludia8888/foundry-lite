import type { OntologyCatalog } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { useCallback } from "react";

import {
  toApplicationRow,
  toChannelRow,
  toClientRow,
  toCompatibilityWindowRow,
  toInstallMetadata,
  toResourceRow,
  toSdkVersionRow,
  type OsdkResourceType,
} from "./developer-model";

/** developerConsole.osdkApplications.list — 앱 목록. */
export function useOsdkApplications() {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    const apps = await client.developerConsole.osdkApplications.list();
    return apps.map(toApplicationRow);
  }, [client]);
  return useFoundryLiteQuery(["developer", "apps"], load);
}

/** developerConsole.osdkApplications.get — 선택 앱 상세(application/clients/resources). */
export function useOsdkApplicationDetail(appId: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    if (!appId) return null;
    const detail = await client.developerConsole.osdkApplications.get(appId);
    return {
      application: toApplicationRow(detail.application),
      clients: detail.clients.map(toClientRow),
      resources: detail.resources.map(toResourceRow),
    };
  }, [client, appId]);
  return useFoundryLiteQuery(["developer", "app-detail", appId], load, {
    enabled: appId !== null,
  });
}

/** developerConsole.osdkApplications.listClients — 클라이언트 목록. */
export function useOsdkClients(appId: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    if (!appId) return [];
    const rows =
      await client.developerConsole.osdkApplications.listClients(appId);
    return rows.map(toClientRow);
  }, [client, appId]);
  return useFoundryLiteQuery(["developer", "clients", appId], load, {
    enabled: appId !== null,
  });
}

/** developerConsole.sdkVersions.list — SDK 버전 목록. */
export function useSdkVersions(appId: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    if (!appId) return [];
    const rows = await client.developerConsole.sdkVersions.list(appId);
    return rows.map(toSdkVersionRow);
  }, [client, appId]);
  return useFoundryLiteQuery(["developer", "sdk-versions", appId], load, {
    enabled: appId !== null,
  });
}

/** developerConsole.sdkVersions.channels — 릴리스 채널 배정 목록. */
export function useSdkChannels(appId: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    if (!appId) return [];
    const rows = await client.developerConsole.sdkVersions.channels(appId);
    return rows.map(toChannelRow);
  }, [client, appId]);
  return useFoundryLiteQuery(["developer", "channels", appId], load, {
    enabled: appId !== null,
  });
}

/** developerConsole.sdkVersions.compatibilityWindows — 호환성 윈도우 목록. */
export function useSdkCompatibilityWindows(appId: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    if (!appId) return [];
    const rows =
      await client.developerConsole.sdkVersions.compatibilityWindows(appId);
    return rows.map(toCompatibilityWindowRow);
  }, [client, appId]);
  return useFoundryLiteQuery(
    ["developer", "compatibility-windows", appId],
    load,
    { enabled: appId !== null },
  );
}

/** developerConsole.sdkVersions.installMetadata — install 증거(버전+채널+윈도우). */
export function useSdkInstallMetadata(appId: string | null) {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    if (!appId) return null;
    const raw =
      await client.developerConsole.sdkVersions.installMetadata(appId);
    return toInstallMetadata(raw);
  }, [client, appId]);
  return useFoundryLiteQuery(["developer", "install-metadata", appId], load, {
    enabled: appId !== null,
  });
}

export type CatalogResource = {
  resourceType: OsdkResourceType;
  resourceApiName: string;
  displayName: string;
};

/** ontology.catalog → 리소스 연결 편집기용 후보 목록(객체/링크/액션/함수). */
export function useCatalogResources() {
  const client = useFoundryLiteClient();
  const load = useCallback(async () => {
    const catalog: OntologyCatalog = await client.ontology.catalog();
    const resources: CatalogResource[] = [];
    for (const object of catalog.objectTypes) {
      resources.push({
        resourceType: "object",
        resourceApiName: object.apiName,
        displayName: object.displayName,
      });
      for (const action of object.actions ?? []) {
        resources.push({
          resourceType: "action",
          resourceApiName: action.apiName,
          displayName: action.displayName,
        });
      }
    }
    for (const link of catalog.linkTypes) {
      resources.push({
        resourceType: "link",
        resourceApiName: link.apiName,
        displayName: link.displayName,
      });
    }
    for (const fn of catalog.functionTypes ?? []) {
      resources.push({
        resourceType: "function",
        resourceApiName: fn.apiName,
        displayName: fn.displayName,
      });
    }
    return resources;
  }, [client]);
  return useFoundryLiteQuery(["developer", "catalog-resources"], load);
}
