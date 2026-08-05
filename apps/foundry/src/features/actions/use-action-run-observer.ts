import type {
  ActionRun,
  ActionRunEvent,
  FoundryLiteGeneratedClient,
} from "@foundry-lite/sdk";
import { useCallback, useEffect, useState } from "react";

const TERMINAL_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "conflict",
  "outcome_unknown",
  "compensation_required",
  "reconciled",
]);

type ActionRunsClient = FoundryLiteGeneratedClient["actions"]["runs"];

type ObserverState = {
  snapshot: ActionRun | null;
  error: Error | null;
  isConnected: boolean;
};

type ObserverSetters = {
  setSnapshot: (run: ActionRun) => void;
  setError: (error: Error | null) => void;
  setConnected: (value: boolean) => void;
};

export function isTerminalActionRun(run: ActionRun | null): boolean {
  return run !== null && TERMINAL_STATUSES.has(run.status);
}

export function useActionRunObserver(
  client: ActionRunsClient,
  runId: string | null,
  initialRun: ActionRun | null,
): ObserverState & { refresh: () => Promise<void> } {
  const [snapshot, setSnapshot] = useState<ActionRun | null>(initialRun);
  const [error, setError] = useState<Error | null>(null);
  const [isConnected, setConnected] = useState(false);

  useEffect(() => setSnapshot(initialRun), [initialRun]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    void observeActionRun(client, runId, controller, {
      setSnapshot,
      setError,
      setConnected,
    });
    return () => controller.abort();
  }, [client, runId]);

  const refresh = useCallback(async () => {
    if (!runId) return;
    setSnapshot(await client.get(runId));
  }, [client, runId]);

  return { snapshot, error, isConnected, refresh };
}

async function observeActionRun(
  client: ActionRunsClient,
  runId: string,
  controller: AbortController,
  setters: ObserverSetters,
): Promise<void> {
  let lastSequence = 0;
  while (!controller.signal.aborted) {
    try {
      const snapshot = await client.get(runId);
      setters.setSnapshot(snapshot);
      setters.setError(null);
      lastSequence = Math.max(lastSequence, snapshot.eventSequence);
      if (isTerminalActionRun(snapshot)) return;
      setters.setConnected(true);
      lastSequence = await consumeActionEvents(
        client,
        runId,
        lastSequence,
        controller,
        setters,
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      setters.setError(toError(error));
    } finally {
      setters.setConnected(false);
    }
    await reconnectDelay(controller.signal);
  }
}

async function consumeActionEvents(
  client: ActionRunsClient,
  runId: string,
  lastSequence: number,
  controller: AbortController,
  setters: ObserverSetters,
): Promise<number> {
  let latest = lastSequence;
  for await (const event of client.events(runId, {
    lastEventId: lastSequence,
    signal: controller.signal,
  })) {
    latest = Math.max(latest, eventSequence(event));
    const snapshot = await client.get(runId);
    setters.setSnapshot(snapshot);
    setters.setError(null);
    if (isTerminalActionRun(snapshot)) break;
  }
  return latest;
}

function eventSequence(event: ActionRunEvent): number {
  const value = Number(event.id ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

function reconnectDelay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, 1_000);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}
