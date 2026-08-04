import type {
  AipBuilderRunResult,
  AipBuilderValidateRequest,
  AipBuilderValidationResult,
  AipEvalRunResult,
  AipFdeCatalog,
  AipFdeRunRequest,
  AipFdeRunResult,
  AipPilotApplicationBundle,
  AipPilotPlan,
  AipPilotPlanRequest,
  AipReleasePromotionResult,
  PromptArtifactReadResult,
} from "@foundry-lite/sdk";
import { createRequestId, idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteProvidedAipAgentRunWithOperationsDetail,
} from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useState } from "react";

import {
  buildBuilderRunRequest,
  defaultAgentPayload,
  defaultEvalRequest,
} from "./aip-model";

export type BuilderRunPayload = {
  validate: AipBuilderValidateRequest;
};

export type PromotePayload = {
  agentVersionId: string;
  targetReleaseChannel: string;
  evalRunId: string;
  policyVersion?: string;
};

/**
 * AIP 워크스페이스 mutation 묶음.
 * mutation마다 idempotency key를 SDK 규약대로 생성해 evidence로 노출한다.
 */
export function useAipWorkspace() {
  const client = useFoundryLiteClient();

  const [fdeCatalog, setFdeCatalog] = useState<AipFdeCatalog | null>(null);
  const [fdeCatalogError, setFdeCatalogError] = useState<unknown>(null);
  useEffect(() => {
    let isCurrent = true;
    void client.aip.fde
      .catalog()
      .then((catalog) => {
        if (isCurrent) setFdeCatalog(catalog);
      })
      .catch((error: unknown) => {
        if (isCurrent) setFdeCatalogError(error);
      });
    return () => {
      isCurrent = false;
    };
  }, [client]);

  const runFde = useFoundryLiteMutation<AipFdeRunResult, AipFdeRunRequest>(
    (payload) => client.aip.fde.run(payload),
    {
      lockKey: (payload) =>
        `aip:fde:${payload.workspaceRef ?? payload.branchId ?? "unscoped"}`,
    },
  );
  const executeFde = useCallback(
    (payload: AipFdeRunRequest) =>
      runFde.execute({
        ...payload,
        agentRunId: payload.agentRunId ?? createRequestId("aip-fde"),
      }),
    [runFde],
  );

  const planPilot = useFoundryLiteMutation<AipPilotPlan, AipPilotPlanRequest>(
    (payload) => client.aip.pilot.plan(payload),
    { lockKey: (payload) => `aip:pilot:plan:${payload.applicationName}` },
  );
  const generatePilot = useFoundryLiteMutation<
    AipPilotApplicationBundle,
    AipPilotPlan
  >(
    (plan) =>
      client.aip.pilot.generate(
        { plan },
        {
          idempotencyKey: idempotencyKey(
            "aip-pilot-generate",
            plan.slug,
          ),
        },
      ),
    { lockKey: (plan) => `aip:pilot:generate:${plan.slug}` },
  );

  // 에이전트 실행 + 연결된 operations run detail (근거 evidence 동시 로드).
  const agent = useFoundryLiteProvidedAipAgentRunWithOperationsDetail();
  const [agentIdempotencyKey, setAgentIdempotencyKey] = useState<string | null>(
    null,
  );

  const runAgent = useCallback(
    (userMessage: string) => {
      const agentRunId = createRequestId("aip-agent");
      const key = idempotencyKey("aip-agent-run", agentRunId);
      setAgentIdempotencyKey(key);
      return agent.execute({
        ...defaultAgentPayload(agentRunId),
        userMessage,
      });
    },
    [agent],
  );

  // 빌더 검증 — validation 결과(ready / blocked + issues)를 그대로 노출.
  const [validateIdempotencyKey, setValidateIdempotencyKey] = useState<
    string | null
  >(null);
  const validateBuilder = useFoundryLiteMutation<
    AipBuilderValidationResult,
    AipBuilderValidateRequest
  >((payload) => client.aip.builder.validate(payload), {
    lockKey: (payload) => `aip:builder-validate:${payload.agentVersionId}`,
  });

  const validate = useCallback(
    (payload: AipBuilderValidateRequest) => {
      setValidateIdempotencyKey(
        idempotencyKey("aip-validate", payload.agentVersionId),
      );
      return validateBuilder.execute(payload);
    },
    [validateBuilder],
  );

  // 빌더 실행 — 그래프 정의를 실제 실행하고 operations run으로 연결.
  const [builderRunIdempotencyKey, setBuilderRunIdempotencyKey] = useState<
    string | null
  >(null);
  const runBuilder = useFoundryLiteMutation<
    AipBuilderRunResult,
    BuilderRunPayload
  >(
    (payload) => {
      const logicRunId = createRequestId("aip-logic");
      const request = buildBuilderRunRequest(payload.validate, logicRunId);
      return client.aip.builder.run(request);
    },
    {
      lockKey: (payload) =>
        `aip:builder-run:${payload.validate.agentVersionId}`,
    },
  );

  const executeBuilder = useCallback(
    (payload: BuilderRunPayload) => {
      setBuilderRunIdempotencyKey(
        idempotencyKey("aip-builder-run", payload.validate.agentVersionId),
      );
      return runBuilder.execute(payload);
    },
    [runBuilder],
  );

  // 평가 스위트 실행.
  // 후보 agent version은 세션마다 고유하게 발급한다. 릴리스는
  // (agent_version, channel) 단위로 고정되므로, 후보를 격리하지 않으면 공유
  // 데모 상태에서 채널이 이미 다른 eval run으로 승격돼 promote가 거부된다.
  const [candidateAgentVersionId] = useState<string>(() =>
    createRequestId("agent.order-ops.candidate"),
  );
  const [evalIdempotencyKey, setEvalIdempotencyKey] = useState<string | null>(
    null,
  );
  const runEval = useFoundryLiteMutation<AipEvalRunResult, string>(
    (candidateReleaseChannel) => {
      // eval run id는 실행마다 고유해야 한다 (재사용 시 서버 충돌).
      const evalRunId = createRequestId("aip-eval");
      return client.aip.evals.run(
        defaultEvalRequest(
          evalRunId,
          candidateReleaseChannel,
          candidateAgentVersionId,
        ),
      );
    },
    { lockKey: () => "aip:eval-run" },
  );

  const executeEval = useCallback(
    (candidateReleaseChannel: string) => {
      setEvalIdempotencyKey(
        idempotencyKey("aip-eval-run", candidateReleaseChannel),
      );
      return runEval.execute(candidateReleaseChannel);
    },
    [runEval],
  );

  // 릴리스 승격 — eval run id를 근거로 채널 승격.
  const [promoteIdempotencyKey, setPromoteIdempotencyKey] = useState<
    string | null
  >(null);
  const promoteRelease = useFoundryLiteMutation<
    AipReleasePromotionResult,
    PromotePayload
  >(
    (payload) =>
      client.aip.releases.promote({
        agentVersionId: payload.agentVersionId,
        targetReleaseChannel: payload.targetReleaseChannel,
        evalRunId: payload.evalRunId,
        policyVersion: payload.policyVersion,
      }),
    { lockKey: (payload) => `aip:release-promote:${payload.evalRunId}` },
  );

  const promote = useCallback(
    (payload: PromotePayload) => {
      setPromoteIdempotencyKey(
        idempotencyKey("aip-release-promote", payload.evalRunId),
      );
      return promoteRelease.execute(payload);
    },
    [promoteRelease],
  );

  // prompt artifact 열람 (실패 시 근거 조회 — aip_prompt_artifact_reader 롤 필요).
  const loadPromptArtifact = useCallback(
    (aiRunId: string, artifactId: string): Promise<PromptArtifactReadResult> =>
      client.operations.runs.promptArtifact(aiRunId, artifactId),
    [client],
  );

  return {
    fdeCatalog,
    fdeCatalogError,
    runFde,
    executeFde,
    planPilot,
    generatePilot,

    agent,
    runAgent,
    agentIdempotencyKey,

    validateBuilder,
    validate,
    validateIdempotencyKey,

    runBuilder,
    executeBuilder,
    builderRunIdempotencyKey,

    runEval,
    executeEval,
    evalIdempotencyKey,
    candidateAgentVersionId,

    promoteRelease,
    promote,
    promoteIdempotencyKey,

    loadPromptArtifact,
  };
}

export type AipWorkspace = ReturnType<typeof useAipWorkspace>;
