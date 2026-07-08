import {
  normalizeFoundryLiteError,
  type FoundryLiteApiError,
  type ObjectFilter,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useEffect, useMemo, useRef, useState } from "react";

export type LiveObjectsState = {
  isStreaming: boolean;
  eventCount: number;
  changeCount: number;
  watermark: number | null;
  error: FoundryLiteApiError | null;
};

/**
 * objects.generic.subscribe(subscriptions/stream)로 현재 필터 결과를 실시간 구독한다.
 * object_changed 이벤트마다 onObjectChanged 콜백을 호출한다.
 */
export function useLiveObjects(
  objectApiName: string | null,
  where: ObjectFilter | undefined,
  enabled: boolean,
  onObjectChanged: () => void,
): LiveObjectsState {
  const client = useFoundryLiteClient();
  const [isStreaming, setIsStreaming] = useState(false);
  const [eventCount, setEventCount] = useState(0);
  const [changeCount, setChangeCount] = useState(0);
  const [watermark, setWatermark] = useState<number | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const onChangeRef = useRef(onObjectChanged);
  onChangeRef.current = onObjectChanged;
  const whereKey = useMemo(() => JSON.stringify(where ?? null), [where]);

  useEffect(() => {
    if (!enabled || !objectApiName) return;
    const controller = new AbortController();
    let isActive = true;
    setIsStreaming(true);
    setEventCount(0);
    setChangeCount(0);
    setError(null);

    const consume = async () => {
      const filter = JSON.parse(whereKey) as ObjectFilter | null;
      const stream = client.objects.generic.subscribe(
        objectApiName,
        { filter, pollIntervalSeconds: 2 },
        { transport: "sse", signal: controller.signal },
      );
      for await (const event of stream) {
        if (!isActive) break;
        const payload = event.payload;
        if (payload.event === "error") continue;
        setEventCount((previous) => previous + 1);
        setWatermark(payload.watermark);
        if (payload.event === "object_changed") {
          setChangeCount((previous) => previous + 1);
          onChangeRef.current();
        }
      }
    };

    consume()
      .catch((caught: unknown) => {
        if (!isActive || controller.signal.aborted) return;
        setError(normalizeFoundryLiteError(caught));
      })
      .finally(() => {
        if (isActive) setIsStreaming(false);
      });

    return () => {
      isActive = false;
      controller.abort();
      setIsStreaming(false);
    };
  }, [client, enabled, objectApiName, whereKey]);

  return { isStreaming, eventCount, changeCount, watermark, error };
}
