import type { PipelineRun } from "@foundry-lite/sdk";

export type PipelineRunOutputEvidence = {
  nodeId: string;
  artifactKind: string;
  plane: string;
  status: string;
  ref: Record<string, unknown>;
  error: Record<string, unknown> | null;
  isLegacyFallback: boolean;
};

export type PipelineRunOutputSummary = {
  total: number;
  committed: number;
  failed: number;
};

export function pipelineRunOutputs(
  run: PipelineRun,
): PipelineRunOutputEvidence[] {
  const outputs = Array.isArray(run.outputs)
    ? run.outputs.flatMap((output, index) => normalizeOutput(output, index))
    : [];
  if (outputs.length > 0) return outputs;
  return legacyDatasetOutput(run);
}

export function summarizePipelineRunOutputs(
  outputs: readonly PipelineRunOutputEvidence[],
): PipelineRunOutputSummary {
  return outputs.reduce(
    (summary, output) => {
      const status = output.status.toUpperCase();
      summary.total += 1;
      if (status === "COMMITTED") summary.committed += 1;
      if (status === "FAILED") summary.failed += 1;
      return summary;
    },
    { total: 0, committed: 0, failed: 0 },
  );
}

export function pipelineRunOutputRefLabel(
  output: PipelineRunOutputEvidence,
): string {
  const datasetRef = textValue(output.ref.datasetRef);
  const versionId = textValue(output.ref.versionId);
  if (datasetRef && versionId) return `${datasetRef} @ ${versionId}`;
  if (datasetRef) return datasetRef;
  const preferredRef = [
    "resourceRef",
    "mediaSetRef",
    "indexRef",
    "virtualTableRef",
    "mappingRef",
  ]
    .map((key) => textValue(output.ref[key]))
    .find(Boolean);
  if (preferredRef) return preferredRef;
  return compactJson(output.ref);
}

export function pipelineRunOutputErrorLabel(
  output: PipelineRunOutputEvidence,
): string | null {
  if (!output.error) return null;
  const code = textValue(output.error.code);
  const message = textValue(output.error.message);
  if (code && message) return `${code} · ${message}`;
  return code ?? message ?? compactJson(output.error);
}

function normalizeOutput(
  value: unknown,
  index: number,
): PipelineRunOutputEvidence[] {
  const output = recordValue(value);
  if (!output) return [];
  return [
    {
      nodeId: textValue(output.nodeId) ?? `output-${index + 1}`,
      artifactKind: textValue(output.artifactKind) ?? "unknown",
      plane: textValue(output.plane) ?? "unknown",
      status: textValue(output.status)?.toUpperCase() ?? "UNKNOWN",
      ref: recordValue(output.ref) ?? {},
      error: recordValue(output.error),
      isLegacyFallback: false,
    },
  ];
}

function legacyDatasetOutput(run: PipelineRun): PipelineRunOutputEvidence[] {
  const datasetRef = textValue(run.outputDatasetRef);
  if (!datasetRef) return [];
  const versionId = textValue(run.outputVersionId);
  return [
    {
      nodeId: "legacy-dataset-output",
      artifactKind: "dataset_version",
      plane: "dataset",
      status: run.status === "failed" ? "FAILED" : "COMMITTED",
      ref: {
        datasetRef,
        ...(versionId ? { versionId } : {}),
      },
      error: recordValue(run.error),
      isLegacyFallback: true,
    },
  ];
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function compactJson(value: Record<string, unknown>): string {
  return Object.keys(value).length > 0 ? JSON.stringify(value) : "-";
}
