import assert from "node:assert/strict";

import {
  pipelineRunOutputErrorLabel,
  pipelineRunOutputRefLabel,
  pipelineRunOutputs,
  summarizePipelineRunOutputs,
} from "../../apps/foundry/src/features/pipelines/pipeline-run-model.ts";

const partialRun = {
  id: "run-partial",
  pipelineId: "pipeline-orders",
  versionId: "version-2",
  status: "partial",
  outputs: [
    {
      nodeId: "orders-output",
      artifactKind: "dataset_version",
      plane: "dataset",
      status: "COMMITTED",
      ref: {
        datasetRef: "analytics.orders",
        versionId: "dataset-version-11",
      },
    },
    {
      nodeId: "failures-output",
      artifactKind: "dataset_version",
      plane: "dataset",
      status: "FAILED",
      ref: { datasetRef: "analytics.order_failures" },
      error: {
        code: "OUTPUT_COMMIT_FAILED",
        message: "health check failed",
      },
    },
  ],
  outputDatasetRef: null,
  outputVersionId: null,
  timeline: [],
  error: null,
  startedAt: "2026-07-16T00:00:00Z",
  completedAt: "2026-07-16T00:00:05Z",
};

const outputs = pipelineRunOutputs(partialRun);
assert.deepEqual(summarizePipelineRunOutputs(outputs), {
  total: 2,
  committed: 1,
  failed: 1,
});
assert.equal(
  pipelineRunOutputRefLabel(outputs[0]),
  "analytics.orders @ dataset-version-11",
);
assert.equal(
  pipelineRunOutputErrorLabel(outputs[1]),
  "OUTPUT_COMMIT_FAILED · health check failed",
);

const legacyOutputs = pipelineRunOutputs({
  ...partialRun,
  id: "run-legacy",
  status: "succeeded",
  outputs: [],
  outputDatasetRef: "analytics.legacy_orders",
  outputVersionId: "dataset-version-9",
});
assert.equal(legacyOutputs.length, 1);
assert.equal(legacyOutputs[0].isLegacyFallback, true);
assert.equal(
  pipelineRunOutputRefLabel(legacyOutputs[0]),
  "analytics.legacy_orders @ dataset-version-9",
);

console.log("pipeline run model contract ok");
