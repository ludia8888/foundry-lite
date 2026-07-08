import type { FoundryLiteApiError, ResourceItem } from "@foundry-lite/sdk";
import {
  idempotencyKey,
  normalizeFoundryLiteError,
  retryWithBackoff,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface ShellResourcesState {
  resources: ResourceItem[];
  favorites: ResourceItem[];
  isLoading: boolean;
  error: FoundryLiteApiError | null;
  reload: () => Promise<void>;
  toggleFavorite: (rid: string) => Promise<void>;
}

/**
 * 셸(사이드바/커맨드 팔레트) 공용 리소스 레지스트리 훅.
 * client.resources.items.search로 서버 리소스를 조회하고,
 * client.resources.favorites.set/delete로 즐겨찾기를 서버에 반영한다.
 * StrictMode-safe: cleanup 이후 도착한 응답은 버린다.
 */
export function useShellResources(isEnabled: boolean): ShellResourcesState {
  const client = useFoundryLiteClient();
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setIsLoading(true);
    try {
      const result = await retryWithBackoff(() =>
        client.resources.items.search(),
      );
      if (!isMountedRef.current) return;
      setResources(result.items);
      setError(null);
    } catch (caught) {
      if (isMountedRef.current) setError(normalizeFoundryLiteError(caught));
    } finally {
      if (isMountedRef.current) setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (isEnabled) void reload();
  }, [isEnabled, reload]);

  const toggleFavorite = useCallback(
    async (rid: string) => {
      const target = resources.find((resource) => resource.rid === rid);
      if (!target) return;
      try {
        const updated = target.isFavorite
          ? await client.resources.favorites.delete(rid, {
              idempotencyKey: idempotencyKey("resource_favorite_delete", rid),
            })
          : await client.resources.favorites.set(rid, {
              idempotencyKey: idempotencyKey("resource_favorite_set", rid),
            });
        if (!isMountedRef.current) return;
        setResources((previous) =>
          previous.map((resource) =>
            resource.rid === updated.rid ? updated : resource,
          ),
        );
      } catch (caught) {
        if (isMountedRef.current) setError(normalizeFoundryLiteError(caught));
      }
    },
    [client, resources],
  );

  const favorites = useMemo(
    () => resources.filter((resource) => resource.isFavorite),
    [resources],
  );

  return { resources, favorites, isLoading, error, reload, toggleFavorite };
}
