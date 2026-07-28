import assert from "node:assert/strict";

import {
  useLlmTrialCount,
  useLlmTrialRows,
  useLlmTrialRunError,
  withUseLlmDraftConfiguration,
} from "../../apps/foundry/src/features/pipelines/pipeline-use-llm-trial-model.ts";

const graph = {
  schemaVersion: 2,
  nodes: [
    {
      id: "source",
      kind: "source",
      descriptorId: "source.dataset",
      specVersion: 1,
      config: { datasetRef: "raw.erp_orders" },
    },
    {
      id: "semantic",
      kind: "transform",
      descriptorId: "transform.use_llm",
      specVersion: 1,
      config: {
        promptTemplate: "Old prompt {{source_status}}",
        mediaReferenceField: "mediaReference",
        outputColumn: "interpretation",
      },
    },
  ],
  edges: [],
};

const draftGraph = withUseLlmDraftConfiguration(graph, "semantic", {
  promptTemplate: "Unsaved prompt {{source_status}}",
  mediaReferenceField: undefined,
  trialCount: 2,
});
assert.ok(draftGraph);
assert.equal(
  draftGraph.nodes[1].config.promptTemplate,
  "Unsaved prompt {{source_status}}",
);
assert.equal("mediaReferenceField" in draftGraph.nodes[1].config, false);
assert.equal(draftGraph.nodes[1].config.trialCount, 2);
assert.equal(graph.nodes[1].config.promptTemplate, "Old prompt {{source_status}}");
assert.equal(graph.nodes[1].config.mediaReferenceField, "mediaReference");
assert.equal(withUseLlmDraftConfiguration(graph, "missing", {}), null);

assert.equal(useLlmTrialCount("1"), 1);
assert.equal(useLlmTrialCount(50), 50);
assert.equal(useLlmTrialCount("0"), null);
assert.equal(useLlmTrialCount("51"), null);
assert.equal(useLlmTrialCount("2.5"), null);

const run = {
  id: "preview-use-llm-1",
  status: "SUCCEEDED",
  commitForbidden: true,
  servingVersionCreated: false,
  artifacts: [],
  outputs: [
    {
      nodeId: "semantic",
      artifactKind: "dataset_version",
      items: [
        {
          order_id: "O-1001",
          source_status: "PENDING",
          interpretation: { label: "manual_review", confidence: 0.94 },
          _pipelineModelTrialEvidence: {
            schemaVersion: 1,
            evidenceKind: "pipeline_semantic_trial",
            input: {
              selectedFields: ["order_id", "source_status"],
              rowSnapshot: {
                order_id: "O-1001",
                source_status: "PENDING",
              },
            },
            request: { requestFingerprint: "sha256:request-1" },
            parseAttempts: [
              {
                attemptNumber: 1,
                stage: "initial_response",
                status: "parsed",
                responseSnapshot: {
                  label: "manual_review",
                  confidence: 0.94,
                },
              },
            ],
            correction: {
              attempted: false,
              attemptCount: 0,
              strategy: "none",
            },
            final: {
              status: "succeeded",
              typedOutput: { label: "manual_review", confidence: 0.94 },
              error: null,
            },
            pins: {
              provider: "anthropic",
              resolvedModelId: "anthropic:claude-sonnet-5",
              resolvedModelRevision: "claude-sonnet-5",
              promptVersionId: "orders@7",
              inputTokens: 80,
              outputTokens: 12,
            },
            noCommit: {
              commitForbidden: true,
              servingVersionCreated: false,
            },
          },
          _pipelineModelEvidence: {
            provider: "legacy-provider-should-not-win",
            cacheEligible: true,
            cacheHit: false,
            cacheStatus: "miss",
          },
        },
        {
          order_id: "O-1002",
          source_status: "APPROVED",
          interpretation: {
            output: null,
            error: {
              code: "PIPELINE_SEMANTIC_OUTPUT_INVALID",
              message: "model output did not match schema",
            },
          },
          _pipelineModelEvidence: {
            provider: "anthropic",
            outputError: {
              code: "PIPELINE_SEMANTIC_OUTPUT_INVALID",
              message: "model output did not match schema",
            },
          },
        },
      ],
    },
  ],
};

const rows = useLlmTrialRows(
  run,
  "semantic",
  "interpretation",
  ["order_id", "source_status"],
);
assert.equal(rows.length, 2);
assert.deepEqual(rows[0].input, {
  order_id: "O-1001",
  source_status: "PENDING",
});
assert.deepEqual(rows[0].output, {
  label: "manual_review",
  confidence: 0.94,
});
assert.equal(rows[0].error, null);
assert.equal(rows[0].evidence.provider, "anthropic");
assert.equal(rows[0].evidence.cacheStatus, "miss");
assert.equal(rows[0].trialEvidence.evidenceKind, "pipeline_semantic_trial");
assert.deepEqual(rows[0].trialEvidence.input.selectedFields, [
  "order_id",
  "source_status",
]);
assert.equal(
  rows[0].trialEvidence.parseAttempts[0].stage,
  "initial_response",
);
assert.equal(rows[0].trialEvidence.parseAttempts[0].status, "parsed");
assert.equal(rows[1].output, null);
assert.equal(rows[1].error.code, "PIPELINE_SEMANTIC_OUTPUT_INVALID");
assert.equal(useLlmTrialRunError(run), null);
assert.deepEqual(
  useLlmTrialRunError({ ...run, status: "FAILED", error: { code: "MODEL_DENIED" } }),
  { code: "MODEL_DENIED" },
);

console.log("Use LLM live trial model contract ok");
