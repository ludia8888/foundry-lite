import type {
  PipelineGraphV2,
  PipelinePreviewRun,
} from "@foundry-lite/sdk";

export type PreviewRecord = Record<string, unknown>;

export type UseLlmTrialRow = {
  id: string;
  input: PreviewRecord;
  output: unknown;
  error: PreviewRecord | null;
  evidence: PreviewRecord | null;
  trialEvidence: PreviewRecord | null;
  source: PreviewRecord;
};

export function useLlmTrialCount(value: string | number): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 50
    ? parsed
    : null;
}

export function withUseLlmDraftConfiguration(
  graph: PipelineGraphV2 | null,
  nodeId: string,
  patch: Record<string, unknown> | null,
): PipelineGraphV2 | null {
  if (!graph || !patch) return null;
  let hasTarget = false;
  const nodes = graph.nodes.map((node) => {
    if (node.id !== nodeId) return node;
    hasTarget = true;
    const config = { ...node.config };
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) delete config[key];
      else config[key] = value;
    }
    return { ...node, config };
  });
  return hasTarget ? { ...graph, nodes } : null;
}

export function useLlmTrialRows(
  run: PipelinePreviewRun | null,
  nodeId: string,
  outputColumn: string,
  inputFields: readonly string[],
): UseLlmTrialRow[] {
  const output = previewOutput(run, nodeId);
  return previewItems(output).map((source, index) =>
    trialRow(source, index, outputColumn, inputFields),
  );
}

export function useLlmTrialRunError(
  run: PipelinePreviewRun | null,
): PreviewRecord | null {
  return run ? recordValue(run.error) : null;
}

export function useLlmRunTrialEvidence(
  run: PipelinePreviewRun | null,
): PreviewRecord | null {
  const error = useLlmTrialRunError(run);
  const details = recordValue(error?.details);
  return recordValue(details?.trialEvidence);
}

function trialRow(
  source: PreviewRecord,
  index: number,
  outputColumn: string,
  inputFields: readonly string[],
): UseLlmTrialRow {
  const legacyEvidence = recordValue(source._pipelineModelEvidence);
  const trialEvidence = recordValue(source._pipelineModelTrialEvidence);
  const trialInput = recordValue(trialEvidence?.input);
  const final = recordValue(trialEvidence?.final);
  const pins = recordValue(trialEvidence?.pins);
  const rawOutput = source[outputColumn];
  const wrappedOutput = recordValue(rawOutput);
  const wrappedError = recordValue(wrappedOutput?.error);
  const evidenceError = recordValue(legacyEvidence?.outputError);
  const finalError = recordValue(final?.error);
  const parseError = lastParseError(trialEvidence);
  const selectedFields = recordValue(trialInput?.selectedFields);
  const rowSnapshot = recordValue(trialInput?.rowSnapshot);
  const evidence = pins
    ? { ...(legacyEvidence ?? {}), ...pins }
    : legacyEvidence;
  return {
    id: rowId(source, index),
    input:
      rowSnapshot ??
      selectedFields ??
      selectedInput(source, inputFields, outputColumn),
    output:
      final && "typedOutput" in final
        ? final.typedOutput
        : wrappedOutput && "output" in wrappedOutput
        ? wrappedOutput.output
        : rawOutput,
    error: finalError ?? wrappedError ?? evidenceError ?? parseError,
    evidence,
    trialEvidence,
    source,
  };
}

function selectedInput(
  source: PreviewRecord,
  inputFields: readonly string[],
  outputColumn: string,
): PreviewRecord {
  if (inputFields.length === 0) {
    return Object.fromEntries(
      Object.entries(source).filter(
        ([key]) =>
          key !== outputColumn &&
          key !== "_pipelineModelEvidence" &&
          key !== "_pipelineModelTrialEvidence",
      ),
    );
  }
  return Object.fromEntries(
    inputFields.map((field) => [field, pathValue(source, field)]),
  );
}

function pathValue(source: PreviewRecord, path: string): unknown {
  return path.split(".").reduce<unknown>((value, segment) => {
    const record = recordValue(value);
    return record ? record[segment] : undefined;
  }, source);
}

function rowId(source: PreviewRecord, index: number): string {
  for (const key of [
    "id",
    "order_id",
    "objectId",
    "mediaItemVersionId",
    "sourceMediaItemVersionId",
  ]) {
    const value = textValue(source[key]);
    if (value) return value;
  }
  return `row-${index + 1}`;
}

function lastParseError(
  trialEvidence: PreviewRecord | null,
): PreviewRecord | null {
  const attempts = recordList(trialEvidence?.parseAttempts);
  for (let index = attempts.length - 1; index >= 0; index -= 1) {
    const attempt = attempts[index];
    if (!attempt) continue;
    const error = recordValue(attempt.error);
    if (error) return error;
  }
  return null;
}

function previewOutput(
  run: PipelinePreviewRun | null,
  targetNodeId: string,
): PreviewRecord | null {
  if (!run) return null;
  const outputs = recordList(run.outputs);
  return (
    outputs.find((output) => textValue(output.nodeId) === targetNodeId) ??
    outputs[0] ??
    null
  );
}

function previewItems(output: PreviewRecord | null): PreviewRecord[] {
  return output ? recordList(output.items) : [];
}

function recordList(value: unknown): PreviewRecord[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const record = recordValue(item);
        return record ? [record] : [];
      })
    : [];
}

function recordValue(value: unknown): PreviewRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as PreviewRecord)
    : null;
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
