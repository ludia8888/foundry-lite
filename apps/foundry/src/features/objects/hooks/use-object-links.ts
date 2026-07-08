import {
  FoundryLiteApiError,
  type ObjectLinkPayload,
  type OntologyCatalogLink,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
  type FoundryLiteOntologyObjectView,
  type FoundryLiteQueryState,
} from "@foundry-lite/sdk/react";
import { useCallback, useMemo } from "react";

import type { ObjectRef } from "../lib/explorer-model";

export type LinkTypeGroup = {
  link: OntologyCatalogLink;
  /** 이 객체 기준으로 도착하는 상대 객체 타입 apiName. */
  targetObjectType: string;
  links: ObjectLinkPayload[];
};

export type ObjectLinksState = FoundryLiteQueryState<LinkTypeGroup[]> & {
  groups: LinkTypeGroup[];
  totalLinkedObjects: number;
};

/** 대상 객체의 양방향 링크 타입 목록 (linksFrom + linksTo, apiName 중복 제거). */
export function linkTypesOf(
  objectView: FoundryLiteOntologyObjectView | null,
): OntologyCatalogLink[] {
  if (!objectView) return [];
  const byApiName = new Map<string, OntologyCatalogLink>();
  for (const link of [...objectView.linksFrom, ...objectView.linksTo]) {
    if (!byApiName.has(link.apiName)) byApiName.set(link.apiName, link);
  }
  return [...byApiName.values()];
}

/** objects.generic.links로 링크 타입별 연결 객체를 일괄 조회한다. */
export function useObjectLinks(
  ref: ObjectRef | null,
  objectView: FoundryLiteOntologyObjectView | null,
): ObjectLinksState {
  const client = useFoundryLiteClient();
  const objectType = ref?.objectType ?? null;
  const objectId = ref?.objectId ?? null;
  const linkTypes = useMemo(() => linkTypesOf(objectView), [objectView]);
  const linkTypesKey = useMemo(
    () => linkTypes.map((link) => link.apiName).join(","),
    [linkTypes],
  );

  const load = useCallback(async () => {
    if (!objectType || !objectId) {
      throw new FoundryLiteApiError(
        0,
        "OBJECT_NOT_SELECTED",
        "링크를 조회할 객체가 선택되지 않았습니다.",
        { missingFields: ["objectType", "objectId"] },
        null,
        false,
      );
    }
    const groups = await Promise.all(
      linkTypes.map(async (link) => ({
        link,
        targetObjectType:
          link.fromObjectType === objectType
            ? link.toObjectType
            : link.fromObjectType,
        links: await client.objects.generic.links(
          objectType,
          objectId,
          link.apiName,
        ),
      })),
    );
    return groups;
  }, [client, linkTypes, objectId, objectType]);

  const query = useFoundryLiteQuery<LinkTypeGroup[]>(
    ["objects", "links", objectType, objectId, linkTypesKey],
    load,
    { enabled: objectType !== null && objectId !== null },
  );

  const groups = query.data ?? [];
  return {
    ...query,
    groups,
    totalLinkedObjects: groups.reduce(
      (total, group) => total + group.links.length,
      0,
    ),
  };
}
