const RUN_TYPE_PREFIXES: readonly [prefix: string, runType: string][] = [
  ["transform_run", "transform"],
  ["source_sync_run", "sync"],
  ["sync_run", "sync"],
  ["index_run", "index"],
  ["mat_run", "materialization"],
  ["materialization_run", "materialization"],
  ["aip-agent-run", "ai"],
  ["aip-builder-run", "ai"],
  ["ai_execution_run", "ai"],
  ["ai_run", "ai"],
  ["writeback", "action_writeback"],
  ["workflow_run", "workflow"],
  ["flite:", "workflow"],
  ["action_writeback_run", "action_writeback"],
  ["action_run", "action"],
  ["outbox_run", "outbox"],
  ["dead_letter_run", "dead_letter"],
  ["audit_run", "audit"],
];

/** run id prefix로 Operations run type을 추정한다. */
export function runTypeFromRunId(runId: string): string {
  return (
    RUN_TYPE_PREFIXES.find(([prefix]) => runId.startsWith(prefix))?.[1] ??
    "transform"
  );
}

export function operationsRunPath(runType: string, runId: string): string {
  return `/operations/runs/${encodeURIComponent(runType)}/${encodeURIComponent(runId)}`;
}

export function operationsRunHref(runId: string, runType?: string): string {
  const resolvedRunType = runType ?? runTypeFromRunId(runId);
  return `/operations?path=${encodeURIComponent(
    operationsRunPath(resolvedRunType, runId),
  )}`;
}
