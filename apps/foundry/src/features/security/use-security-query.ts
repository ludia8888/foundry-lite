import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import { normalizeFoundryLiteError, retryWithBackoff } from "@foundry-lite/sdk";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface SecurityQueryState<TData> {
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
 * React StrictMode의 모의 재마운트 이후 응답을 버리고 영원히 loading에 머무는
 * 버그가 있다. 앱 셸이 StrictMode로 감싸져 있으므로(main.tsx), 화면 폴더 안에서
 * 동일 규약(SDK client + retryWithBackoff + normalizeFoundryLiteError)을 지키는
 * 대체 훅을 사용한다. projects 화면의 use-projects-query.ts와 동일한 패턴이다.
 */
export function useSecurityQuery<TData>(
  key: readonly unknown[],
  load: () => Promise<TData>,
): SecurityQueryState<TData> {
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestKey = useMemo(() => JSON.stringify(key), [key]);
  const isMountedRef = useRef(true);
  const hasDataRef = useRef(false);
  const loadRef = useRef(load);
  loadRef.current = load;

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
      const nextData = await retryWithBackoff(() => loadRef.current());
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
  }, []);

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

/** Record<string, unknown> row에서 첫 번째로 존재하는 문자열 값을 안전하게 읽는다. */
export function readRowString(
  row: Record<string, unknown> | null | undefined,
  ...keys: string[]
): string | null {
  if (!row) return null;
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}
