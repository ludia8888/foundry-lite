import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import { normalizeFoundryLiteError, retryWithBackoff } from "@foundry-lite/sdk";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface ProjectsQueryState<TData> {
  data: TData | null;
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  requestId: string | null;
  retryable: boolean;
  reload: () => Promise<void>;
}

/**
 * StrictMode-safe SDK 조회 훅.
 *
 * SDK의 useFoundryLiteQuery(packages/sdk-ts/src/react.ts)는 cleanup에서
 * mountedRef를 false로 만든 뒤 effect 본문에서 다시 true로 세우지 않아,
 * React StrictMode의 모의 재마운트 이후 모든 응답을 버리고 영원히 loading에
 * 머무는 버그가 있다. 이 앱 셸은 StrictMode로 감싸져 있으므로(main.tsx),
 * 화면 폴더 안에서 동일 규약(SDK client + retryWithBackoff +
 * normalizeFoundryLiteError)을 지키는 대체 훅을 사용한다.
 */
export function useProjectsQuery<TData>(
  key: readonly unknown[],
  load: () => Promise<TData>,
): ProjectsQueryState<TData> {
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestKey = useMemo(() => JSON.stringify(key), [key]);
  const isMountedRef = useRef(true);
  const hasDataRef = useRef(false);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    if (hasDataRef.current) setIsRefreshing(true);
    else setIsLoading(true);
    try {
      const nextData = await retryWithBackoff(load);
      if (!isMountedRef.current) return;
      hasDataRef.current = true;
      setData(nextData);
      setError(null);
    } catch (caught) {
      if (isMountedRef.current) setError(normalizeFoundryLiteError(caught));
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, [load]);

  useEffect(() => {
    void reload();
    // requestKey가 바뀌면 재조회한다.
  }, [reload, requestKey]);

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
