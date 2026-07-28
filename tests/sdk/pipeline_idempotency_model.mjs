import assert from "node:assert/strict";

import {
  createPipelineIdempotencyRegistry,
  runRetainedPipelineMutation,
} from "../../apps/foundry/src/features/pipelines/pipeline-idempotency.ts";

let sequence = 0;
const registry = createPipelineIdempotencyRegistry(
  (scope) => `${scope}-key-${++sequence}`,
);
const originalPayload = {
  pipelineId: "orders",
  schedule: { timezone: "UTC", intervalSeconds: 60 },
};
const samePayloadDifferentKeyOrder = {
  schedule: { intervalSeconds: 60, timezone: "UTC" },
  pipelineId: "orders",
};

const firstKey = registry.acquire("pipeline-schedule-upsert", originalPayload);
assert.equal(
  registry.acquire(
    "pipeline-schedule-upsert",
    samePayloadDifferentKeyOrder,
  ),
  firstKey,
  "the same semantic retry must retain its idempotency key",
);
assert.notEqual(
  registry.acquire("pipeline-schedule-upsert", {
    ...originalPayload,
    schedule: { timezone: "UTC", intervalSeconds: 120 },
  }),
  firstKey,
  "changed mutation content is a new intent",
);

const headerSafeRegistry = createPipelineIdempotencyRegistry(
  (scope, intentToken) => `${scope}-${intentToken}`,
);
assert.equal(
  headerSafeRegistry.acquire("pipeline-propose", {
    branchId: "branch-1",
    title: 'A title with "quoted" JSON content',
  }),
  "pipeline-propose-intent-1",
  "semantic payload JSON must stay inside the registry and never leak into the HTTP header key",
);
assert.equal(
  headerSafeRegistry.acquire("pipeline-propose", {
    branchId: "branch-2",
    title: "Another proposal",
  }),
  "pipeline-propose-intent-2",
  "each new semantic intent receives a distinct header-safe token",
);

await assert.rejects(
  runRetainedPipelineMutation(
    registry,
    "pipeline-run-start",
    { pipelineId: "orders", versionId: "v1" },
    () => {},
    async () => {
      throw new Error("response lost");
    },
  ),
  /response lost/,
);
let retryKey = "";
await runRetainedPipelineMutation(
  registry,
  "pipeline-run-start",
  { versionId: "v1", pipelineId: "orders" },
  (key) => {
    retryKey = key;
  },
  async (key) => key,
);
assert.equal(retryKey, "pipeline-run-start-key-3");

const nextDeliberateRunKey = registry.acquire("pipeline-run-start", {
  pipelineId: "orders",
  versionId: "v1",
});
assert.equal(
  nextDeliberateRunKey,
  "pipeline-run-start-key-4",
  "success releases the retained intent for a later deliberate run",
);

console.log("pipeline idempotency model contract ok");
