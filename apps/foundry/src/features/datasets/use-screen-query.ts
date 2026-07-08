import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import { normalizeFoundryLiteError } from "@foundry-lite/sdk";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface ScreenQueryState<TData> {
  data: TData | null;
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  isRefreshing: boolean;
  requestId: string | null;
  retryable: boolean;
  reload: () => Promise<void>;
}

interface ScreenQueryOptions<TData> {
  enabled?: boolean;
  onSuccess?: (data: TData) => void;
}

/**
 * SDK client 호출을 감싸는 화면 전용 조회 훅.
 *
 * SDK의 useFoundryLiteQuery는 React StrictMode(개발 모드)에서 mountedRef가
 * 시뮬레이션 unmount 후 복구되지 않아 결과 commit이 영구히 스킵되는 버그가 있어
 * (packages/sdk-ts/src/react.ts) 이 화면에서는 동일 시그니처의 자체 훅으로
 * SDK named method/recipe 호출을 감싼다. 에러는 normalizeFoundryLiteError로
 * 표준화해 request id/retryability evidence를 유지한다.
 */
export function useScreenQuery<TData>(
  key: readonly unknown[],
  load: () => Promise<TData>,
  options: ScreenQueryOptions<TData> = {},
): ScreenQueryState<TData> {
  const { enabled = true, onSuccess } = options;
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(enabled);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestKey = useMemo(() => JSON.stringify(key), [key]);

  const loadRef = useRef(load);
  loadRef.current = load;
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;
  const hasDataRef = useRef(false);
  const runSequenceRef = useRef(0);

  const reload = useCallback(async () => {
    const sequence = runSequenceRef.current + 1;
    runSequenceRef.current = sequence;
    if (hasDataRef.current) setIsRefreshing(true);
    else setIsLoading(true);
    try {
      const nextData = await loadRef.current();
      if (sequence !== runSequenceRef.current) return;
      hasDataRef.current = true;
      setData(nextData);
      setError(null);
      onSuccessRef.current?.(nextData);
    } catch (caught) {
      if (sequence !== runSequenceRef.current) return;
      setError(normalizeFoundryLiteError(caught));
    } finally {
      if (sequence === runSequenceRef.current) {
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
