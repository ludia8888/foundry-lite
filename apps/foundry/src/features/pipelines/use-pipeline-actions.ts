import type {
  PipelineBranch,
  PipelineGraph,
  PipelineProposal,
  PipelineRun,
  PipelineSchedule,
  PipelineVersion,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { createPipelineBuilderRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useCallback, useMemo, useState } from "react";

export type SaveGraphPayload = {
  branchId: string;
  graph: PipelineGraph;
  expectedFingerprint: string;
};

export type CreateBranchPayload = { pipelineId: string; name: string };
export type ProposePayload = {
  branchId: string;
  title: string;
  description: string;
};
export type DecideProposalPayload = {
  proposalId: string;
  decision: "approve" | "reject";
  comment: string;
};
export type StartRunPayload = { pipelineId: string; versionId: string | null };
export type DeployPayload = { pipelineId: string; versionId: string };
export type UpsertSchedulePayload = {
  pipelineId: string;
  versionId: string;
  schedule: Record<string, unknown>;
};

type ActionCallbacks = {
  onGraphSaved?: (branch: PipelineBranch) => void;
  onBranchCreated?: (branch: PipelineBranch) => void;
  onProposalChanged?: () => void;
  onVersionChanged?: () => void;
  onRunChanged?: (run: PipelineRun) => void;
};

/** Pipeline Builder mutation 묶음. idempotency key는 SDK 규약대로 생성해 evidence로 노출한다. */
export function usePipelineActions(callbacks: ActionCallbacks = {}) {
  const client = useFoundryLiteClient();
  const recipe = useMemo(() => createPipelineBuilderRecipe(client), [client]);
  const [lastIdempotencyKey, setLastIdempotencyKey] = useState<string | null>(
    null,
  );

  const saveGraph = useFoundryLiteMutation(
    (payload: SaveGraphPayload) =>
      recipe.updateGraph(payload.branchId, {
        graph: payload.graph,
        expectedFingerprint: payload.expectedFingerprint,
      }),
    {
      lockKey: (payload) => `pipelines:save:${payload.branchId}`,
      onSuccess: callbacks.onGraphSaved,
    },
  );

  const createBranch = useFoundryLiteMutation(
    (payload: CreateBranchPayload) => {
      const key = idempotencyKey(
        "pipeline-branch-create",
        `${payload.pipelineId}:${payload.name}`,
      );
      setLastIdempotencyKey(key);
      return recipe.createBranch(payload, { idempotencyKey: key });
    },
    {
      lockKey: (payload) =>
        `pipelines:branch-create:${payload.pipelineId}:${payload.name}`,
      onSuccess: callbacks.onBranchCreated,
    },
  );

  const propose = useFoundryLiteMutation(
    (payload: ProposePayload) => {
      const key = idempotencyKey("pipeline-propose", payload.branchId);
      setLastIdempotencyKey(key);
      return recipe.propose(
        payload.branchId,
        { title: payload.title, description: payload.description || null },
        { idempotencyKey: key },
      );
    },
    {
      lockKey: (payload) => `pipelines:propose:${payload.branchId}`,
      onSuccess: () => callbacks.onProposalChanged?.(),
    },
  );

  const decideProposal = useFoundryLiteMutation(
    (payload: DecideProposalPayload): Promise<PipelineProposal> =>
      recipe.decideProposal(payload.proposalId, {
        decision: payload.decision,
        comment: payload.comment || null,
      }),
    {
      lockKey: (payload) => `pipelines:decide:${payload.proposalId}`,
      onSuccess: () => callbacks.onProposalChanged?.(),
    },
  );

  const executeProposal = useFoundryLiteMutation(
    (payload: { proposalId: string }): Promise<PipelineVersion> =>
      recipe.executeProposal(payload.proposalId),
    {
      lockKey: (payload) => `pipelines:execute:${payload.proposalId}`,
      onSuccess: () => {
        callbacks.onProposalChanged?.();
        callbacks.onVersionChanged?.();
      },
    },
  );

  const withdrawProposal = useFoundryLiteMutation(
    (payload: { proposalId: string }): Promise<PipelineProposal> =>
      recipe.withdrawProposal(payload.proposalId),
    {
      lockKey: (payload) => `pipelines:withdraw:${payload.proposalId}`,
      onSuccess: () => callbacks.onProposalChanged?.(),
    },
  );

  const deployVersion = useFoundryLiteMutation(
    (payload: DeployPayload) =>
      recipe.deploy(payload.pipelineId, payload.versionId),
    {
      lockKey: (payload) => `pipelines:deploy:${payload.versionId}`,
      onSuccess: () => callbacks.onVersionChanged?.(),
    },
  );

  const startRun = useFoundryLiteMutation(
    (payload: StartRunPayload): Promise<PipelineRun> =>
      recipe.startRun(payload.pipelineId, { versionId: payload.versionId }),
    {
      lockKey: (payload) => `pipelines:run:${payload.pipelineId}`,
      onSuccess: callbacks.onRunChanged,
    },
  );

  const cancelRun = useFoundryLiteMutation(
    (payload: { runId: string }): Promise<PipelineRun> =>
      recipe.cancelRun(payload.runId),
    {
      lockKey: (payload) => `pipelines:cancel:${payload.runId}`,
      onSuccess: callbacks.onRunChanged,
    },
  );

  const upsertSchedule = useFoundryLiteMutation(
    (payload: UpsertSchedulePayload): Promise<PipelineSchedule> =>
      recipe.upsertSchedule(payload.pipelineId, {
        versionId: payload.versionId,
        schedule: payload.schedule,
        enabled: true,
      }),
    {
      lockKey: (payload) => `pipelines:schedule-upsert:${payload.pipelineId}`,
    },
  );

  const deleteSchedule = useFoundryLiteMutation(
    (payload: { pipelineId: string }) =>
      recipe.deleteSchedule(payload.pipelineId),
    {
      lockKey: (payload) => `pipelines:schedule-delete:${payload.pipelineId}`,
    },
  );

  const loadRunTimeline = useCallback(
    (runId: string) => recipe.timeline(runId),
    [recipe],
  );

  return {
    recipe,
    lastIdempotencyKey,
    saveGraph,
    createBranch,
    propose,
    decideProposal,
    executeProposal,
    withdrawProposal,
    deployVersion,
    startRun,
    cancelRun,
    upsertSchedule,
    deleteSchedule,
    loadRunTimeline,
  };
}

export type PipelineActions = ReturnType<typeof usePipelineActions>;
