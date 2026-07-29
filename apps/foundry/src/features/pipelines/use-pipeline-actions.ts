import type {
  PipelineBranch,
  PipelineGraph,
  PipelineGraphV2,
  PipelineProposal,
  PipelineRun,
  PipelineSchedule,
  PipelineScheduleSpec,
  PipelineVersion,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { createPipelineBuilderRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useCallback, useMemo, useState } from "react";

import {
  createPipelineIdempotencyRegistry,
  runRetainedPipelineMutation,
} from "./pipeline-idempotency";

export type SaveGraphPayload = {
  branchId: string;
  graph: PipelineGraph | PipelineGraphV2;
  expectedFingerprint: string;
};

export type CreateBranchPayload = { pipelineId: string; name: string };
export type RebaseBranchPayload = {
  branchId: string;
  expectedFingerprint: string;
};
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
export type AssignProposalPayload = {
  proposalId: string;
  assigneeUserId: string;
};
export type StartRunPayload = { pipelineId: string; versionId: string | null };
export type DeployPayload = { pipelineId: string; versionId: string };
export type UpsertSchedulePayload = {
  pipelineId: string;
  versionId: string;
  schedule: PipelineScheduleSpec;
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
  const idempotencyRegistry = useMemo(
    () => createPipelineIdempotencyRegistry(idempotencyKey),
    [],
  );
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
    (payload: CreateBranchPayload) =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-branch-create",
        payload,
        setLastIdempotencyKey,
        (key) => recipe.createBranch(payload, { idempotencyKey: key }),
      ),
    {
      lockKey: (payload) =>
        `pipelines:branch-create:${payload.pipelineId}:${payload.name}`,
      onSuccess: callbacks.onBranchCreated,
    },
  );

  const rebaseBranch = useFoundryLiteMutation(
    (payload: RebaseBranchPayload): Promise<PipelineBranch> =>
      recipe.rebase(payload.branchId, {
        expectedFingerprint: payload.expectedFingerprint,
      }),
    {
      lockKey: (payload) => `pipelines:rebase:${payload.branchId}`,
      onSuccess: callbacks.onGraphSaved,
    },
  );

  const propose = useFoundryLiteMutation(
    (payload: ProposePayload) =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-propose",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.propose(
            payload.branchId,
            { title: payload.title, description: payload.description || null },
            { idempotencyKey: key },
          ),
      ),
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

  const assignProposal = useFoundryLiteMutation(
    (payload: AssignProposalPayload): Promise<PipelineProposal> =>
      recipe.assignProposal(payload.proposalId, {
        assigneeUserId: payload.assigneeUserId,
      }),
    {
      lockKey: (payload) => `pipelines:assign:${payload.proposalId}`,
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
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-deploy",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.deploy(
            payload.pipelineId,
            payload.versionId,
            {},
            { idempotencyKey: key },
          ),
      ),
    {
      lockKey: (payload) => `pipelines:deploy:${payload.versionId}`,
      onSuccess: () => callbacks.onVersionChanged?.(),
    },
  );

  const startRun = useFoundryLiteMutation(
    (payload: StartRunPayload): Promise<PipelineRun> =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-run-start",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.startRun(
            payload.pipelineId,
            { versionId: payload.versionId },
            { idempotencyKey: key },
          ),
      ),
    {
      lockKey: (payload) => `pipelines:run:${payload.pipelineId}`,
      onSuccess: callbacks.onRunChanged,
    },
  );

  const cancelRun = useFoundryLiteMutation(
    (payload: { runId: string }): Promise<PipelineRun> =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-run-cancel",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.cancelRun(
            payload.runId,
            { reason: "Cancelled from Pipeline Builder" },
            { idempotencyKey: key },
          ),
      ),
    {
      lockKey: (payload) => `pipelines:cancel:${payload.runId}`,
      onSuccess: callbacks.onRunChanged,
    },
  );

  const upsertSchedule = useFoundryLiteMutation(
    (payload: UpsertSchedulePayload): Promise<PipelineSchedule> =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-schedule-upsert",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.upsertSchedule(
            payload.pipelineId,
            {
              versionId: payload.versionId,
              schedule: payload.schedule,
              enabled: true,
            },
            { idempotencyKey: key },
          ),
      ),
    {
      lockKey: (payload) => `pipelines:schedule-upsert:${payload.pipelineId}`,
    },
  );

  const pauseSchedule = useFoundryLiteMutation(
    (payload: { pipelineId: string }): Promise<PipelineSchedule> =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-schedule-pause",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.pauseSchedule(payload.pipelineId, {
            idempotencyKey: key,
          }),
      ),
    {
      lockKey: (payload) => `pipelines:schedule-pause:${payload.pipelineId}`,
    },
  );

  const resumeSchedule = useFoundryLiteMutation(
    (payload: { pipelineId: string }): Promise<PipelineSchedule> =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-schedule-resume",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.resumeSchedule(payload.pipelineId, {
            idempotencyKey: key,
          }),
      ),
    {
      lockKey: (payload) => `pipelines:schedule-resume:${payload.pipelineId}`,
    },
  );

  const deleteSchedule = useFoundryLiteMutation(
    (payload: { pipelineId: string }) =>
      runRetainedPipelineMutation(
        idempotencyRegistry,
        "pipeline-schedule-delete",
        payload,
        setLastIdempotencyKey,
        (key) =>
          recipe.deleteSchedule(payload.pipelineId, {
            idempotencyKey: key,
          }),
      ),
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
    rebaseBranch,
    propose,
    assignProposal,
    decideProposal,
    executeProposal,
    withdrawProposal,
    deployVersion,
    startRun,
    cancelRun,
    upsertSchedule,
    pauseSchedule,
    resumeSchedule,
    deleteSchedule,
    loadRunTimeline,
  };
}

export type PipelineActions = ReturnType<typeof usePipelineActions>;
