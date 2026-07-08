import type {
  SourceConnection,
  SourceManagedSync,
  SourceManagedSyncRun,
} from "@foundry-lite/sdk";
import { idempotencyKey, normalizeFoundryLiteError } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback, useRef, useState } from "react";

import { sanitizeIdentifier } from "../source-model";

export type BatchSyncPhase =
  "pending" | "creating" | "created" | "running" | "succeeded" | "failed";

export interface BatchSyncItem {
  tableName: string;
  syncName: string;
  datasetRef: string;
  phase: BatchSyncPhase;
  sync: SourceManagedSync | null;
  run: SourceManagedSyncRun | null;
  createKey: string;
  runKey: string | null;
  errorMessage: string | null;
}

export interface BatchSyncInput {
  tables: readonly string[];
  prefix: string;
  databaseUrlSecretRef: string;
  shouldRunAfterCreate: boolean;
}

/** 접두사 적용 대상 데이터셋 ref: sync.{접두사}{테이블}. */
export function buildBatchDatasetRef(prefix: string, tableName: string) {
  return `sync.${sanitizeIdentifier(`${prefix}${tableName}`)}`;
}

function buildBatchSyncName(sourceName: string, prefix: string, table: string) {
  return `${sourceName}_${sanitizeIdentifier(`${prefix}${table}`)}_sync`;
}

/**
 * 선택 테이블별 managedSyncs.create(+ 옵션 startRun)를 순차 실행하고
 * 테이블별 request key / run id 증거를 수집한다 (postgres_jdbc 계약).
 */
export function useBatchSyncCreation(source: SourceConnection) {
  const client = useFoundryLiteClient();
  const [items, setItems] = useState<BatchSyncItem[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const keysRef = useRef(new Map<string, string>());

  const stableKey = useCallback((scope: string, name: string) => {
    const cacheKey = `${scope}:${name}`;
    const existing = keysRef.current.get(cacheKey);
    if (existing) return existing;
    const created = idempotencyKey(scope, name);
    keysRef.current.set(cacheKey, created);
    return created;
  }, []);

  const updateItem = useCallback(
    (syncName: string, patch: Partial<BatchSyncItem>) => {
      setItems((current) =>
        current.map((item) =>
          item.syncName === syncName ? { ...item, ...patch } : item,
        ),
      );
    },
    [],
  );

  const runOne = useCallback(
    async (item: BatchSyncItem, input: BatchSyncInput) => {
      updateItem(item.syncName, { phase: "creating" });
      const sync = await client.sources.managedSyncs.create(
        {
          syncName: item.syncName,
          sourceName: source.sourceName,
          displayName: `${source.displayName} · ${item.tableName}`,
          sourceType: "postgres_jdbc",
          capability: "batch",
          targetDatasetRef: item.datasetRef,
          mode: "SNAPSHOT",
          schedule: { mode: "manual" },
          configSummary: {
            databaseUrlSecretRef: input.databaseUrlSecretRef,
            tableName: item.tableName,
          },
        },
        { idempotencyKey: item.createKey },
      );
      if (!input.shouldRunAfterCreate) {
        updateItem(item.syncName, { phase: "created", sync });
        return;
      }
      updateItem(item.syncName, { phase: "running", sync });
      const runKey = stableKey("source_explore_sync_run", item.syncName);
      updateItem(item.syncName, { runKey });
      const run = await client.sources.managedSyncs.startRun(
        item.syncName,
        { triggerType: "manual" },
        { idempotencyKey: runKey },
      );
      updateItem(item.syncName, {
        phase: run.status === "failed" ? "failed" : "succeeded",
        run,
        errorMessage: run.error ? JSON.stringify(run.error) : null,
      });
    },
    [client, source, stableKey, updateItem],
  );

  const execute = useCallback(
    async (input: BatchSyncInput) => {
      const initialItems: BatchSyncItem[] = input.tables.map((tableName) => {
        const syncName = buildBatchSyncName(
          source.sourceName,
          input.prefix,
          tableName,
        );
        return {
          tableName,
          syncName,
          datasetRef: buildBatchDatasetRef(input.prefix, tableName),
          phase: "pending" as const,
          sync: null,
          run: null,
          createKey: stableKey("source_explore_sync", syncName),
          runKey: null,
          errorMessage: null,
        };
      });
      setItems(initialItems);
      setIsRunning(true);
      try {
        for (const item of initialItems) {
          try {
            await runOne(item, input);
          } catch (caught) {
            const normalized = normalizeFoundryLiteError(caught);
            updateItem(item.syncName, {
              phase: "failed",
              errorMessage: normalized.message,
            });
          }
        }
      } finally {
        setIsRunning(false);
      }
    },
    [source.sourceName, runOne, stableKey, updateItem],
  );

  const reset = useCallback(() => {
    setItems([]);
    keysRef.current = new Map();
  }, []);

  return { items, isRunning, execute, reset };
}
