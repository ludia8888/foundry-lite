import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import { normalizeFoundryLiteError } from "@foundry-lite/sdk";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type SafeQueryState<TData> = {
  data: TData | null;
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  requestId: string | null;
  retryable: boolean;
  reload(): Promise<TData | null>;
};

/**
 * StrictMode-safe 조회 프리미티브 (FoundryLiteQueryState와 동일 계약).
 *
 * 배경: SDK의 useFoundryLiteQuery는 mountedRef cleanup 패턴 때문에 React
 * StrictMode(이 앱의 main.tsx에서 활성)에서 두 번째 mount 이후 모든 상태
 * 업데이트를 건너뛰어 isLoading이 영구히 true로 남는다. SDK 소스는 이 화면
 * 폴더 밖이라 수정할 수 없으므로, 같은 계약의 안전한 래퍼로 SDK client
 * named method 호출을 감싼다 (직접 fetch 없음).
 */
export function useSafeQuery<TData>(
  key: readonly unknown[],
  load: () => Promise<TData>,
  options: { enabled?: boolean } = {},
): SafeQueryState<TData> {
  const { enabled = true } = options;
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(enabled);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestKey = useMemo(() => JSON.stringify(key), [key]);
  const epochRef = useRef(0);
  const hasDataRef = useRef(false);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  const reload = useCallback(async () => {
    const epoch = epochRef.current + 1;
    epochRef.current = epoch;
    if (hasDataRef.current) setIsRefreshing(true);
    else setIsLoading(true);
    try {
      const next = await loadRef.current();
      if (epochRef.current !== epoch) return next;
      hasDataRef.current = true;
      setData(next);
      setError(null);
      return next;
    } catch (caught) {
      if (epochRef.current === epoch)
        setError(normalizeFoundryLiteError(caught));
      return null;
    } finally {
      if (epochRef.current === epoch) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void reload();
  }, [enabled, reload, requestKey]);

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
