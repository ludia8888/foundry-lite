import type {
  FoundryLiteApiError,
  PipelineGraphV2,
  PipelinePreviewLimitsRequest,
  PipelinePreviewRun,
} from "@foundry-lite/sdk";
import {
  createInFlightActionLock,
  idempotencyKey,
  normalizeFoundryLiteError,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const POLL_INTERVAL_MS = 650;
const TERMINAL_STATUSES = new Set([
  "SUCCEEDED",
  "PARTIAL",
  "FAILED",
  "CANCELLED",
]);

type PreviewRunInput = {
  branchId: string | null;
  graph: PipelineGraphV2 | null;
  targetNodeId: string | null;
  limits?: PipelinePreviewLimitsRequest;
};

/** 현재 저장되지 않은 그래프를 no-commit preview run으로 시작하고 terminal 상태까지 조회한다. */
export function usePipelinePreviewRun({
  branchId,
  graph,
  targetNodeId,
  limits,
}: PreviewRunInput) {
  const client = useFoundryLiteClient();
  const graphSignature = useMemo(() => JSON.stringify(graph), [graph]);
  const limitsSignature = useMemo(() => JSON.stringify(limits), [limits]);
  const startLock = useMemo(() => createInFlightActionLock(), []);
  const epochRef = useRef(0);
  const startIdempotencyKeyRef = useRef<string | null>(null);
  const [run, setRun] = useState<PipelinePreviewRun | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);

  useEffect(() => {
    epochRef.current += 1;
    startLock.clear();
    startIdempotencyKeyRef.current = null;
    setRun(null);
    setError(null);
    setIsStarting(false);
    setIsCancelling(false);
  }, [branchId, graphSignature, limitsSignature, startLock, targetNodeId]);

  const start = useCallback(async () => {
    if (!branchId || !graph || !targetNodeId) return null;
    const lockKey = `${branchId}:${targetNodeId}:${graphSignature}:${limitsSignature}`;
    return startLock.run(lockKey, async () => {
      const epoch = epochRef.current;
      const requestKey =
        startIdempotencyKeyRef.current ??
        idempotencyKey("pipeline-preview", `${branchId}:${targetNodeId}`);
      startIdempotencyKeyRef.current = requestKey;
      setIsStarting(true);
      setError(null);
      try {
        const created = await client.pipelines.previewRuns.create(
          branchId,
          { graph, targetNodeId, limits },
          { idempotencyKey: requestKey },
        );
        if (epochRef.current === epoch) {
          startIdempotencyKeyRef.current = null;
          setRun(created);
        }
        return created;
      } catch (caught) {
        if (epochRef.current === epoch) {
          setError(normalizeFoundryLiteError(caught));
        }
        return null;
      } finally {
        if (epochRef.current === epoch) setIsStarting(false);
      }
    });
  }, [
    branchId,
    client,
    graph,
    graphSignature,
    limits,
    limitsSignature,
    startLock,
    targetNodeId,
  ]);

  const cancel = useCallback(async () => {
    if (!run || isTerminalPreviewStatus(run.status)) return null;
    const epoch = epochRef.current;
    setIsCancelling(true);
    setError(null);
    try {
      const cancelled = await client.pipelines.previewRuns.cancel(run.id);
      if (epochRef.current === epoch) setRun(cancelled);
      return cancelled;
    } catch (caught) {
      if (epochRef.current === epoch) {
        setError(normalizeFoundryLiteError(caught));
      }
      return null;
    } finally {
      if (epochRef.current === epoch) setIsCancelling(false);
    }
  }, [client, run]);

  useEffect(() => {
    const previewRunId = run?.id;
    const previewStatus = run?.status;
    if (!previewRunId || !previewStatus || isTerminalPreviewStatus(previewStatus)) {
      return;
    }
    const epoch = epochRef.current;
    let isCancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const current = await client.pipelines.previewRuns.get(previewRunId);
        if (!isCancelled && epochRef.current === epoch) {
          setRun(current);
          setError(null);
        }
      } catch (caught) {
        if (!isCancelled && epochRef.current === epoch) {
          setError(normalizeFoundryLiteError(caught));
        }
      } finally {
        if (!isCancelled && epochRef.current === epoch) {
          timer = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
        }
      }
    };
    timer = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      isCancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [client, run?.id, run?.status]);

  const isRunning =
    isStarting || Boolean(run && !isTerminalPreviewStatus(run.status));

  return {
    run,
    error,
    isStarting,
    isCancelling,
    isRunning,
    canStart: Boolean(branchId && graph && targetNodeId),
    start,
    cancel,
  };
}

export function isTerminalPreviewStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status.toUpperCase());
}
