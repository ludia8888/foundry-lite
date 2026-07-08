import {
  normalizeFoundryLiteError,
  retryWithBackoff,
  type FoundryLiteApiError,
} from "@foundry-lite/sdk";
import type { FoundryLiteQueryState } from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * StrictMode-safe 조회 프리미티브 (FoundryLiteQueryState 호환).
 *
 * SDK의 useFoundryLiteQuery는 (1) StrictMode 재마운트 시 mountedRef가 false로
 * 남아 결과 반영이 영구히 막히고, (2) load 콜백 identity에 effect가 묶여 있어
 * 일부 SDK 훅(inline load)이 무한 refetch 루프에 빠진다.
 * 이 프리미티브는 요청 세대(generation) 추적으로 stale 응답만 무시하고,
 * effect를 key 문자열에만 묶어 두 문제를 모두 피한다.
 * (SDK 수정은 이 화면 범위 밖 — notes 참고)
 */
export function useLineageQuery<TData>(
  key: readonly unknown[],
  load: () => Promise<TData>,
  options: { enabled?: boolean } = {},
): FoundryLiteQueryState<TData> {
  const { enabled = true } = options;
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(enabled);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const requestKey = useMemo(() => JSON.stringify(key), [key]);
  const generationRef = useRef(0);
  const hasDataRef = useRef(false);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  const reload = useCallback(async (): Promise<TData | null> => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    if (hasDataRef.current) setIsRefreshing(true);
    else setIsLoading(true);
    try {
      const nextData = await retryWithBackoff(() => loadRef.current());
      if (generationRef.current !== generation) return nextData;
      hasDataRef.current = true;
      setData(nextData);
      setError(null);
      return nextData;
    } catch (caught) {
      if (generationRef.current === generation) {
        setError(normalizeFoundryLiteError(caught));
      }
      return null;
    } finally {
      if (generationRef.current === generation) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
    // requestKey 변경 시에만 identity가 바뀌도록 유지한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  useEffect(() => {
    if (!enabled) return;
    void reload();
  }, [enabled, reload]);

  return {
    data,
    error,
    isLoading,
    isRefreshing,
    requestId: error?.requestId ?? null,
    retryable: error?.retryable ?? false,
    reload,
  };
}
