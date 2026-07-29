import type { PipelineRun } from "@foundry-lite/sdk";
import { useCallback, useEffect, useState } from "react";

import type { PipelineActions } from "./use-pipeline-actions";

const TERMINAL = new Set(["succeeded", "partial", "failed", "cancelled"]);

type ObserverState = {
  snapshot: PipelineRun | null;
  error: Error | null;
  isConnected: boolean;
};

type ObserverSetters = {
  setSnapshot: (run: PipelineRun) => void;
  setError: (error: Error | null) => void;
  setConnected: (value: boolean) => void;
};

export function usePipelineRunObserver(
  recipe: PipelineActions["recipe"],
  runId: string | null,
  initialRun: PipelineRun | null,
): ObserverState & { refresh: () => Promise<void> } {
  const [snapshot, setSnapshot] = useState<PipelineRun | null>(initialRun);
  const [error, setError] = useState<Error | null>(null);
  const [isConnected, setConnected] = useState(false);

  useEffect(() => {
    setSnapshot(initialRun);
  }, [initialRun]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    void observeRun(recipe, runId, controller, {
      setSnapshot,
      setError,
      setConnected,
    });
    return () => controller.abort();
  }, [recipe, runId]);

  const refresh = useCallback(async () => {
    if (!runId) return;
    setSnapshot(await recipe.getRun(runId));
  }, [recipe, runId]);

  return { snapshot, error, isConnected, refresh };
}

async function observeRun(
  recipe: PipelineActions["recipe"],
  runId: string,
  controller: AbortController,
  setters: ObserverSetters,
): Promise<void> {
  let cursor = 0;
  while (!controller.signal.aborted) {
    try {
      const snapshot = await recipe.getRun(runId);
      setters.setSnapshot(snapshot);
      cursor = Math.max(cursor, runSequence(snapshot));
      if (isTerminal(snapshot)) return;
      setters.setConnected(true);
      cursor = await consumeEvents(recipe, runId, cursor, controller, setters);
    } catch (error) {
      if (controller.signal.aborted) return;
      setters.setError(toError(error));
    } finally {
      setters.setConnected(false);
    }
    await reconnectDelay(controller.signal);
  }
}

async function consumeEvents(
  recipe: PipelineActions["recipe"],
  runId: string,
  cursor: number,
  controller: AbortController,
  setters: ObserverSetters,
): Promise<number> {
  let latest = cursor;
  for await (const event of recipe.runEvents(runId, {
    lastEventId: cursor,
    signal: controller.signal,
  })) {
    latest = Math.max(latest, Number(event.id ?? 0));
    const snapshot = await recipe.getRun(runId);
    setters.setSnapshot(snapshot);
    setters.setError(null);
    if (isTerminal(snapshot)) break;
  }
  return latest;
}

function runSequence(run: PipelineRun): number {
  return Number(run.orchestration?.lastEventSequence ?? 0);
}

function isTerminal(run: PipelineRun): boolean {
  return TERMINAL.has(String(run.status));
}

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

function reconnectDelay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, 1000);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}
