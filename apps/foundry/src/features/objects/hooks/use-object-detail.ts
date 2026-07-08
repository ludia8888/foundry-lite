import { FoundryLiteApiError, type GenericObject } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
  type FoundryLiteQueryState,
} from "@foundry-lite/sdk/react";
import { useCallback } from "react";

import type { ObjectRef } from "../lib/explorer-model";

export type ObjectDetailState = FoundryLiteQueryState<GenericObject> & {
  object: GenericObject | null;
};

/** objects.generic.get(explain=true)으로 단일 객체 + lineage 근거를 조회한다. */
export function useObjectDetail(ref: ObjectRef | null): ObjectDetailState {
  const client = useFoundryLiteClient();
  const objectType = ref?.objectType ?? null;
  const objectId = ref?.objectId ?? null;

  const load = useCallback(() => {
    if (!objectType || !objectId) {
      throw new FoundryLiteApiError(
        0,
        "OBJECT_NOT_SELECTED",
        "상세를 조회할 객체가 선택되지 않았습니다.",
        { missingFields: ["objectType", "objectId"] },
        null,
        false,
      );
    }
    return client.objects.generic.get(objectType, objectId, { explain: true });
  }, [client, objectId, objectType]);

  const query = useFoundryLiteQuery<GenericObject>(
    ["objects", "detail", objectType, objectId],
    load,
    { enabled: objectType !== null && objectId !== null },
  );

  return { ...query, object: query.data };
}
